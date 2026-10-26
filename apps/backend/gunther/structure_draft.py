"""Layer 2: measurable clues on evidence blocks, and a complete rule draft of the plan.

``compute_clues`` attaches short facts to blocks: repetition at page edges, type
size against the body, caption labels, bibliography entries, notes closing a
page, sentence continuation across breaks. ``draft_plan`` turns them into a
plan in which every placeable block is placed once or dropped with a reason.
Neither writes text; the Analysis model may correct the draft afterwards.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from gunther.document_model import (
    BLOCK_ID,
    Block,
    Drop,
    Evidence,
    Figure,
    Note,
    Part,
    Plan,
    Section,
    Table,
)

FURNITURE = frozenset({"page_header", "page_footer", "page_number"})
TEXTUAL = frozenset(
    {"text", "title", "section_header", "list_item", "caption", "footnote", "other"}
)
HEADING_LABELS = frozenset({"title", "section_header"})

TOP, BOTTOM = 120_000, 900_000  # page edge bands, parts per million of the page height
PAGE_AREA = 1_000_000 * 1_000_000

_CAPTION = re.compile(
    r"^(?P<prefix>(?i:extended\s+data|supplementary|supplemental|supporting)\s+)?"
    r"(?P<kind>(?i:fig(?:ure)?s?\.?|table|tab\.|abbildung|tabelle)|图|表)\s*"
    r"(?P<number>[A-Z]?\d{1,3}(?:\.\d{1,2})?|[IVX]{1,6})\b"
)
_CAPTION_DELIMITER = re.compile(r"\s*[|:：.‒–—-]")
_RUNS_ON = re.compile(r"(?i:continued on (?:the )?next page)\W*$")
_CONTINUED = re.compile(r"^\W*(?i:continued(?: (?:from|on) (?:the )?(?:previous|next) page)?)\W*$")
_NUMBERING = re.compile(
    r"^\s*(?:(?P<arabic>\d{1,2}(?:\.\d{1,2}){0,4})\.?|(?P<roman>[IVXL]{1,6})\.|"
    r"(?P<letter>[A-Z])(?:\.(?P<sub>\d{1,2}(?:\.\d{1,2}){0,3}))?(?P<dot>\.)?)\s+(?=\S)"
)
_HEADING_KINDS = tuple(
    (kind, re.compile(pattern, re.I))
    for kind, pattern in (
        ("references", r"references?(?: and notes)?|bibliography|literature cited|works cited|"
                       r"cited literature|reference list|参考文献"),
        ("abstract", r"abstract|résumé|resumen|zusammenfassung|摘要|要旨"),
        ("summary", r"(?:(?:author|lay|plain[- ]language|executive|editor['’]?s?)\s+)?summary|"
                    r"synopsis|significance(?: statement)?|概要|内容提要"),
        ("keywords", r"key\s?words?|index terms|subject terms|关键词|关键字"),
        ("availability", r"(?:data|code|software|materials?)(?:(?:,|,? and)\s*(?:data|code|"
                         r"software|materials?))*\s+availability(?: statement)?|availability"
                         r"(?: of (?:data|code|software|materials?|supporting data)(?: and "
                         r"(?:data|code|software|materials?))?)?|data access(?:ibility)?"
                         r"(?: statement)?|accession (?:codes?|numbers?)"),
        ("acknowledgements", r"acknowledge?ments?|致谢"),
        ("appendix", r"appendi(?:x|ces)(?:\s+[A-Z0-9]{1,3}\b.*|\s*[:：.].*)?|附录.*"),
        ("supplementary", r"supplementary (?:information|materials?|data|files?|figures|tables)|"
                          r"supporting information|additional files?|extended data(?: figures)?|"
                          r"electronic supplementary materials?"),
        ("glossary", r"glossary|abbreviations?|nomenclature|acronyms|list of "
                     r"(?:abbreviations|symbols)"),
        ("notes", r"notes|end\s?notes|footnotes"),
        ("back-matter", r"competing (?:financial )?interests?(?: statement)?|conflicts? of "
                        r"interests?(?: statement| disclosure)?|declaration of (?:competing )?"
                        r"interests?|declarations?(?: of interests?)?|(?:financial )?"
                        r"disclosures?(?: statement)?|authors?['’]? contributions?(?: statement)?|"
                        r"contributions|contributors|authors?['’]? information|author orcids?|"
                        r"orcid(?: ids?)?|funding(?: information| statement| sources?)?|financial "
                        r"support|sources? of funding|additional information|publisher['’]?s? "
                        r"note|reprints?(?: and permissions?)?|rights and permissions|permissions|"
                        r"peer review(?: information| file| history)?|decision letter(?: and "
                        r"author response)?|open access|licen[cs]e(?: information)?|copyright"
                        r"(?: statement| information)?|correspondence(?: and requests for "
                        r"materials)?|consent for publication"),
    )
)
_RUN_INS = (
    ("abstract", re.compile(r"^(?i:abstract|summary)(?:\s*[.:：—–|]\s*|\s+(?=[A-Z0-9“\"(]))")),
    ("keywords", re.compile(r"^(?i:key\s?words?|index terms|subject terms)\s*[:：.—–-]")),
    ("availability", re.compile(
        r"^(?i:(?:data|code|software)(?:\s+and\s+(?:data|code|materials?|software))?\s+"
        r"availability(?: statement)?|availability of (?:data|code|materials?)(?: and "
        r"materials?)?)(?:\s*[:：.]|\s+(?=[A-Z]))"
    )),
    ("acknowledgements", re.compile(r"^(?i:acknowledge?ments?)(?:\s*[:：.]|\s+(?=[A-Z]))")),
    ("back-matter", re.compile(
        r"^(?:(?i:competing (?:financial )?interests?|conflicts? of interests?(?: statement)?|"
        r"declaration of (?:competing )?interests?|author contributions?|publisher['’]?s note|"
        r"peer review (?:information|history)|reprints? and permissions?|open access|"
        r"correspondence and requests)\b|(?i:funding|financial support|contributions|"
        r"correspondence|citation|edited by|reviewed by|editor|specialty section|licen[cs]e|"
        r"copyright)\s*[:：.]|(?i:funding|financial support)\s+(?=[A-Z])|"
        r"(?i:copyright)\s*(?:©|\(c\))|©)"
    )),
)
# The sections most papers share; other headings set like them may be their subsections.
_IMRAD = re.compile(
    r"introduction|background|(?:(?:materials?|methods?) and )?(?:methods?|materials?)|methodology|"
    r"online methods|results?(?: and discussions?)?|discussions?(?: and conclusions?)?|"
    r"conclusions?(?: and (?:discussion|outlook|future work))?|concluding remarks",
    re.I,
)
# A permission or licence notice states it in its first sentence.
_LICENCE = re.compile(
    r"(?i)\blicen[cs]ed (?:under|to)\b|\bcreative commons\b|\bgrants? (?:\w+ ){0,2}permission\b|"
    r"\bpermission to (?:reproduce|use|copy|distribute|reprint)\b|\ball rights reserved\b|©|"
    r"\(c\)\s*(?:19|20)\d{2}|\bcopyright\s*(?:\(c\)|(?:19|20)\d{2})|\bopen access article\b"
)
_DATES = re.compile(
    r"^(?i:received|accepted|revised|published(?: online)?|available online|publication date|"
    r"first published|issue date|issued|出版日期|发布日期|接收日期|修订日期)\b\s*[:：]?\s*\S"
)
_DATE_VALUE = re.compile(r"\b(?:19|20)\d{2}\b|\bx{2}\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|^\W*(?i:e-?mail)\s*:")
_INSTITUTION = re.compile(
    r"\b(?:universit(?:y|ies|ät|at|é|e|à|a|ad|eit)|institut(?:e|o|ion)?s?|department|dept\.|"
    r"laborator(?:y|ies|ium|io|oire)|school of|college|faculty|faculté|centre|center|hospital|"
    r"clinic|academy|academia|foundation|corporation|inc\.|ltd\b|gmbh|graduate school|"
    r"polytechnic|politecnico|ministry|agency|council)",
    re.I,
)
_LINK_LINE = re.compile(r"(?i:doi\s*:?\s*)?(?:https?://\S+|www\.\S+|10\.\d{4,9}/\S+)")
_PAGE_NUMBER = re.compile(
    r"(?i:(?:page|p\.)\s*)?\d{1,4}(?:\s*(?:(?i:of)|/)\s*\d{1,4})?|[ivxlc]{1,7}"
)
_NOTE_MARKER = re.compile(r"^\s*(?:[*†‡§¶#¤]|\d{1,2}\s+[A-Z])")
_REF_LABEL = re.compile(
    r"^\s*(?:\[\s*[\w+.\- ]{1,14}\]|\(\d{1,4}\)|\d{1,4}\.\s|\d{1,3}(?=[A-Z][a-zA-Z]?[.\s]))"
)
_REF_AUTHOR = re.compile(
    r"^\s*(?:(?:van|von|de|der|den|del|da|di|du|le|la|dos|ten|ter)\s+)*[A-Z][\w'’\-]+"
    r"(?:\s[A-Z][\w'’\-]+){0,2},?\s+(?:[A-Z]\.?\s?-?){1,4}(?:[,.&;]|\s+(?:and|&)\s)"
)
_REF_YEAR = re.compile(r"\b(?:1[6-9]|20)\d{2}[a-z]?\b")
_REF_LINK = re.compile(r"(?i)\bdoi\b|\b10\.\d{4,9}/|arxiv|https?://")
_LIST_START = re.compile(r"^\s*(?:\(?\d{1,3}[.)]\s|\[\d{1,3}\]|[•●▪◦‣∙·*–-]\s)")
_TERMINAL = re.compile(r"[.!?。！？:：;；][\"'”’)\]]*$")
_PARTICLES = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le", "dos", "das",
    "y", "bin", "al", "el", "ten", "ter", "and", "et", "of", "for", "the", "in", "at", "on",
})


def compute_clues(evidence: Evidence) -> None:
    """Attach measurable facts to each block as short strings (see the plan document)."""
    for block in evidence.blocks:
        block.clues = []
        if block.extra.get("sameItem"):
            block.clues.append("same-item:" + block.extra["sameItem"])
    doc = _Doc(evidence)
    _typography(doc)
    _edges(doc)
    _text_patterns(doc)
    _margin_column(doc)
    _page_notes(doc)
    _picture_clues(doc)
    _title_clues(doc)
    _bookmark_clues(doc)
    _continuations(doc)
    claimed = {image for group in _match_floats(doc) for image in group.images}
    for picture in doc.pictures:
        if picture.id not in claimed:
            picture.clues.append("no-caption")


def _bookmark_clues(doc: _Doc) -> None:
    """A block printing a bookmark's title on the bookmark's page is that heading.

    Bookmarks are the document's own navigation, so they decide what is a heading
    and how deep it sits ("bookmark:2"), wherever the type alone is unclear.
    """
    bookmarks = doc.evidence.manifest.get("bookmarks") or []
    if not bookmarks:
        return
    wanted: dict[tuple[int, str], int] = {}
    for mark in bookmarks:
        key = _key(str(mark.get("title") or ""))
        if key and isinstance(mark.get("page"), int):
            wanted.setdefault((mark["page"], key), int(mark.get("level") or 1))
    if not wanted:
        return
    top = min(wanted.values())
    for block in doc.blocks:
        if block.label not in TEXTUAL or not block.placeable or len(block.text) > 200:
            continue
        level = wanted.get((block.page, _key(block.text)))
        if level is not None:
            block.clues.append(f"bookmark:{level - top + 1}")


def draft_plan(evidence: Evidence) -> Plan:
    """A complete plan from the clues: every placeable block placed once or dropped."""
    if not any(block.clues for block in evidence.blocks):
        compute_clues(evidence)
    return _Draft(evidence).plan()


# Shared facts --------------------------------------------------------------------


def _key(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _area(box) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _x_overlap(a, b) -> int:
    return min(a[2], b[2]) - max(a[0], b[0])


def _within(inner, outer, ratio: float = 0.85) -> bool:
    overlap = max(0, _x_overlap(inner, outer)) * max(
        0, min(inner[3], outer[3]) - max(inner[1], outer[1])
    )
    return overlap >= ratio * max(1, _area(inner))


def _pad(box, pad: int):
    return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)


def _union(boxes):
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def _region_boxes(block: Block) -> list[tuple[int, tuple]]:
    result = []
    for region in block.regions:
        box = region.get("bboxPpm")
        if box and isinstance(region.get("page"), int):
            result.append((region["page"], (box[0], box[1], box[0] + box[2], box[1] + box[3])))
    return result


def _has(block: Block, *prefixes: str) -> bool:
    return any(clue.startswith(prefixes) for clue in block.clues)


def _clue(block: Block, prefix: str) -> str | None:
    return next((clue[len(prefix):] for clue in block.clues if clue.startswith(prefix)), None)


def _same_item(first: Block, second: Block) -> bool:
    """Two regions the layout parser read as one item (see ``same-item:``). Lines of one
    item that the evidence set apart (``splitFrom``: a wide gap, a change of type) are
    separate blocks, not one item's regions."""
    ref = first.extra.get("documentRef")
    if first.extra.get("splitFrom") or second.extra.get("splitFrom"):
        return False
    return first is not second and ref is not None and second.extra.get("documentRef") == ref


def _new_paragraph(first: Block, second: Block) -> bool:
    """The second part is in other type, or the first ends a sentence and the second
    plainly opens something new."""
    if not _same_type(first.font_size, second.font_size) or _has(
        second, "heading:", "run-in:", "caption:"
    ):
        return True
    return bool(_TERMINAL.search(first.text.rstrip())) and bool(
        _LIST_START.match(second.text) or second.marker or _has(second, "reference-entry")
    )


def _section_name(text: str) -> str:
    stripped = _NUMBERING.sub("", text.strip(), count=1) if len(text) < 200 else text
    return re.sub(r"\s+", " ", stripped).strip(" .:：-–—").casefold()


def _heading_kind(text: str) -> str | None:
    if len(text) > 120:
        return None
    name = _section_name(text)
    return next((kind for kind, pattern in _HEADING_KINDS if pattern.fullmatch(name)), None)


def _half(size: float | None) -> float:
    return round((size or 0) * 2) / 2


def _caption_label(block: Block) -> tuple[str, str] | None:
    """('figure' | 'table', printed number) when the block opens with a caption label."""
    text = block.text.strip()
    match = _CAPTION.match(text)
    if not match:
        return None
    rest = text[match.end():]
    if not (
        block.label == "caption" or _CAPTION_DELIMITER.match(rest)
        or _CONTINUED.match(rest) or not rest.strip()
    ):
        return None
    word = match["kind"].casefold()
    kind = "table" if word.startswith(("tab", "表")) else "figure"
    number = match["number"]
    prefix = (match["prefix"] or "").casefold()
    if prefix.startswith("extended"):
        number = "E" + number
    elif prefix and not number.startswith("S"):
        number = "S" + number
    return kind, number


class _Doc:
    """Read-only views of the evidence shared by the clue and draft passes."""

    def __init__(self, evidence: Evidence):
        self.evidence = evidence
        self.body = evidence.body_font_size
        self.blocks = [block for block in evidence.blocks if block.placeable]
        self.by_id = {block.id: block for block in evidence.blocks}
        self.position = {block.id: index for index, block in enumerate(evidence.blocks)}
        self.by_page: dict[int, list[Block]] = defaultdict(list)
        for block in self.blocks:
            if block.box and isinstance(block.page, int):
                self.by_page[block.page].append(block)
        self.pictures = [b for b in self.blocks if b.label == "picture" and b.box]
        # A stitched item's regions share its reference; links point at its first block.
        self.by_ref: dict = {}
        for block in evidence.blocks:
            self.by_ref.setdefault(block.extra.get("documentRef"), block)
        self.first_page = min(
            (b.page for b in self.blocks if b.label not in FURNITURE and isinstance(b.page, int)),
            default=1,
        )
        self.next_placeable: dict[str, str] = {}
        for first, second in zip(self.blocks, self.blocks[1:], strict=False):
            self.next_placeable[first.id] = second.id

    def small(self, block: Block) -> bool:
        return bool(self.body and block.font_size and block.font_size < self.body * 0.95)

    def prose(self, block: Block, length: int = 100) -> bool:
        """Running text in the body's type size, the strongest boundary on a page."""
        return (
            block.label in {"text", "list_item"} and len(block.text) > length
            and not (self.body and block.font_size and block.font_size < self.body * 0.95)
        )

    def union(self, block: Block):
        """Every region of the block on the page of its first region."""
        boxes = [box for page, box in _region_boxes(block) if page == block.page]
        return _union(boxes) or block.box

    def near_picture(self, block: Block) -> bool:
        """Within a figure's reach: panel letters, axis numbers and keys sit here."""
        return any(
            picture.page == block.page and _area(picture.box) >= 0.02 * PAGE_AREA
            and _within(block.box, _pad(picture.box, 15_000))
            for picture in self.pictures
        )

    def furniture(self, block: Block) -> bool:
        return block.label in FURNITURE or _has(block, "repeats:", "page-number")


# Clues ---------------------------------------------------------------------------


def _typography(doc: _Doc) -> None:
    if not doc.body:
        return
    for block in doc.blocks:
        if block.font_size and block.label in TEXTUAL | {"formula", "code"}:
            if block.font_size < doc.body * 0.95:
                block.clues.append("small-type")
            elif block.font_size >= doc.body * 1.15:
                block.clues.append("large-type")


def _edges(doc: _Doc) -> None:
    """Page edges, running heads and feet repeated on several pages, page numbers."""

    def band(block):
        return "top" if block.box[3] < TOP else "bottom" if block.box[1] > BOTTOM else None

    textual = [b for b in doc.blocks if b.box and b.label in TEXTUAL | FURNITURE]
    known = {
        _key(b.text) for b in textual if b.label in {"page_header", "page_footer"} and _key(b.text)
    }
    pages: dict[tuple, set] = defaultdict(set)
    for block in textual:
        edge, key = band(block), _key(block.text)
        if edge and key and len(block.text) <= 120:
            pages[(key, edge)].add(block.page)
            if stripped := re.sub(r"\d", "", key):
                pages[("#" + stripped, edge)].add(block.page)
    headings = [b.font_size for b in textual if b.label in HEADING_LABELS and b.font_size]
    largest = max(headings, default=0)
    numbers = _page_numbers(doc, [
        b for b in textual
        if band(b) or b.label in FURNITURE or b.box[0] > 900_000 or b.box[2] < 100_000
    ])
    for block in doc.blocks:
        if not block.box:
            continue
        edge = band(block)
        if edge:
            block.clues.append("edge:" + edge)
        key, text = _key(block.text), block.text.strip()
        if block not in textual or not key:
            continue
        # Panel letters and axis numbers repeat at page edges too; they sit by a picture.
        declared = block.label in FURNITURE
        if not declared and doc.near_picture(block):
            continue
        if block.id in numbers:
            block.clues.append("page-number")
        if not declared and len(key) < 3:
            continue
        residue = key
        for running in sorted(known, key=len, reverse=True):
            residue = residue.replace(running, "")
        repeated = edge and (
            len(pages[(key, edge)]) >= 2
            or len(pages[("#" + re.sub(r"\d", "", key), edge)]) >= 2
        )
        running_head = key in known or not residue
        guarded = largest and block.font_size and block.font_size >= largest
        if (declared or not guarded) and (
            (edge and (repeated or running_head))
            or (running_head and len(text) <= 40 and block.box[3] < 250_000)
        ):
            upper = block.box[1] < 500_000
            block.clues.append("repeats:" + (edge or ("top" if upper else "bottom")))


def _page_numbers(doc: _Doc, candidates: list[Block]) -> set[str]:
    """Numbers at a page edge that keep step with the page: printed page = page + offset."""
    found = {}
    for block in candidates:
        text = block.text.strip()
        if not _PAGE_NUMBER.fullmatch(text) or (
            block.label not in FURNITURE and doc.near_picture(block)
        ):
            continue
        digits = re.search(r"\d+", text)
        found[block.id] = (block, int(digits[0]) - block.page if digits else None)
    pages = defaultdict(set)
    for block, offset in found.values():
        if offset is not None:
            pages[offset].add(block.page)
    steady = {offset for offset, seen in pages.items() if len(seen) >= 2}
    return {
        block_id for block_id, (block, offset) in found.items()
        if block.label in FURNITURE or offset in steady or (not steady and offset is not None)
    }


def _text_patterns(doc: _Doc) -> None:
    for block in doc.blocks:
        if block.label not in TEXTUAL:
            continue
        text = block.text.strip()
        stripped = re.sub(r"\s", "", text)
        if block.label in {"text", "other", "section_header"} and not block.marker and (
            len(stripped) <= 2
            or (len(stripped) <= 24 and not re.search(r"[^\W\d_]|\d{4}", stripped))
        ):
            block.clues.append("stray-glyphs")
        if caption := _caption_label(block):
            block.clues.append(f"caption:{caption[0]}:{caption[1]}")
        heading_like = block.label in HEADING_LABELS or len(text) <= 80
        kind = _heading_kind(text) if heading_like and block.label != "caption" else None
        if kind:
            block.clues.append("heading:" + kind)
        elif len(text) > 12:
            for name, pattern in _RUN_INS:
                if pattern.match(text) and (name != "abstract" or len(text) > 150):
                    block.clues.append("run-in:" + name)
                    break
        if _DATES.match(text) and _DATE_VALUE.search(text) and len(text) <= 200:
            block.clues.append("dates")
        if _EMAIL.search(text):
            block.clues.append("email")
        if _INSTITUTION.search(text):
            block.clues.append("institution")
        if _LINK_LINE.fullmatch(text):
            block.clues.append("link-line")
        if _LICENCE.search(re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:300]):
            block.clues.append("licence")
        if _reference_entry(block, text):
            block.clues.append("reference-entry")


def _reference_entry(block: Block, text: str) -> bool:
    if not 25 <= len(text) <= 2500 or block.label in HEADING_LABELS:
        return False
    numbered = bool(block.marker and re.fullmatch(r"\W*\w{1,6}\W*", block.marker))
    opening = numbered or _REF_LABEL.match(text) or _REF_AUTHOR.match(text)
    return bool(opening and (_REF_YEAR.search(text) or _REF_LINK.search(text)))


def _margin_column(doc: _Doc) -> None:
    """Small type in a narrow strip beside the body column, never overlapping it."""
    if not doc.body:
        return
    for blocks in doc.by_page.values():
        prose = [b.box for b in blocks if b.label == "text" and len(b.text) > 200
                 and b.font_size and b.font_size >= doc.body * 0.95]
        if not prose:
            continue
        width = sorted(box[2] - box[0] for box in prose)[len(prose) // 2]
        found = []
        for block in blocks:
            if block.label in TEXTUAL - {"caption"} and not _has(block, "caption:") and (
                doc.small(block) and _in_strip(block, blocks, prose, width)
            ):
                block.clues.append("margin-column")
                found.append(block.box)
        if not found:
            continue
        column = _union(found)
        for block in blocks:
            if "margin-column" in block.clues or _has(block, "caption:"):
                continue
            unsized = block.label in TEXTUAL and not block.font_size
            if (
                (unsized or block.label == "picture") and _in_strip(block, blocks, prose, width)
                and _x_overlap(block.box, column) > 0.5 * (block.box[2] - block.box[0])
            ):
                block.clues.append("margin-column")


def _in_strip(block: Block, blocks: list[Block], prose: list, width: int) -> bool:
    """Beside every prose paragraph, narrow, and sharing its column with narrow text only."""
    box = block.box
    if box[2] - box[0] >= 0.6 * width or not all(box[2] <= p[0] or box[0] >= p[2] for p in prose):
        return False
    mates = [o for o in blocks if o is not block and o.label in TEXTUAL
             and _x_overlap(o.box, box) > 0.5 * (box[2] - box[0])]
    return all(o.box[2] - o.box[0] < 0.6 * width for o in mates)


def _page_notes(doc: _Doc) -> None:
    """Smaller type closing a column is the page's foot; with a leading marker it is a note."""
    if not doc.body:
        return
    for page, blocks in doc.by_page.items():
        column_text = [b for b in blocks if b.label in TEXTUAL and not doc.furniture(b)]
        foot: set[str] = set()
        for block in sorted(blocks, key=lambda b: b.box[1]):
            if block.label not in {"text", "footnote", "list_item"}:
                continue
            column = [o for o in column_text if o is not block and _x_overlap(o.box, block.box) > 0]
            size = block.font_size or 0
            if doc.small(block) and block.box[1] > 600_000 and not any(
                o.box[1] >= block.box[3] - 2_000 and (o.font_size or 0) > size + 0.2
                for o in column
            ):
                above = [o for o in column if o.box[3] <= block.box[1] + 2_000]
                nearest = max(above, key=lambda o: o.box[3], default=None)
                # The foot starts where the type gets smaller, not inside small-type prose.
                if nearest is None or nearest.id in foot or (nearest.font_size or 0) > size + 0.2:
                    foot.add(block.id)
                    block.clues.append("page-foot")
            marked = bool(
                _NOTE_MARKER.match(block.text) or (block.marker and not block.marker[0].isdigit())
                or any(s.get("start") == 0 and s.get("script") == "super" for s in block.inline)
            )
            # Marked notes at the foot of the opening page can share the body's size.
            opening_foot = (
                page == doc.first_page and block.box[1] > 850_000
                and not any(o.box[1] > block.box[3] for o in column if o.label != "footnote")
            )
            if marked and (block.id in foot or opening_foot):
                block.clues.append("note-marker")


def _picture_clues(doc: _Doc) -> None:
    for picture in doc.pictures:
        if any(
            other is not picture and other.page != picture.page
            and all(abs(a - b) < 20_000 for a, b in zip(other.box, picture.box, strict=True))
            for other in doc.pictures
        ):
            picture.clues.append("repeats-image")
    by_page = defaultdict(list)
    for picture in doc.pictures:
        by_page[picture.page].append(picture)
    for block in doc.blocks:
        if block.label in TEXTUAL and block.box and block.page in by_page:
            covering = [p for p in by_page[block.page] if _within(block.box, p.box)]
            if covering:
                owner = min(covering, key=lambda p: _area(p.box))
                block.clues.append("on-picture:" + owner.id)


def _title_clues(doc: _Doc) -> None:
    title = _find_title(doc)
    start = doc.position[title[0]] if title else None
    page = doc.by_id[title[0]].page if title else None
    for block in doc.blocks:
        if block.page == doc.first_page:
            block.clues.append("first-page")
        if start is not None and block.page == page and doc.position[block.id] < start:
            block.clues.append("before-title")


def _flow_skip(doc: _Doc, block: Block) -> bool:
    """Blocks a sentence can step over: furniture, floats, notes, margin text and the
    statements set apart from the body (availability, funding, acknowledgements)."""
    return (
        block.label in FURNITURE | {"footnote", "caption", "picture", "table"}
        or _has(block, "repeats:", "page-number", "stray-glyphs", "margin-column", "note-marker",
                "caption:", "on-picture:", "link-line", "run-in:availability",
                "run-in:back-matter", "run-in:acknowledgements")
        or bool(_CONTINUED.match(block.text) and len(block.text) < 60)
    )


def _same_type(a: float | None, b: float | None) -> bool:
    return a is None or b is None or abs(a - b) < 0.5


def _continuations(doc: _Doc) -> None:
    """A sentence broken by a column or page break continues in the next block.

    Text in other type (a caption running on beside the page break) is stepped over:
    the sentence continues in its own type.
    """
    flow: dict[int, list[tuple]] = defaultdict(list)
    for block in doc.blocks:
        if not _flow_skip(doc, block):
            for page, box in _region_boxes(block):
                flow[page].append((block.id, box, block.font_size))
    recent: list[Block] = []  # the latest text block of each type size
    for block in doc.blocks:
        if _flow_skip(doc, block):
            continue
        if block.label not in {"text", "list_item"}:
            recent.clear()
            continue
        same = [b for b in recent if _same_type(b.font_size, block.font_size)]
        if same and _continues(same[-1], block, flow):
            block.clues.append("continues:" + same[-1].id)
        recent = [b for b in recent if b not in same] + [block]


def _continues(previous: Block, block: Block, flow: dict) -> bool:
    """The first part ends its column and the second opens a later column."""
    ends, opens = _region_boxes(previous), _region_boxes(block)
    if not ends or not opens or len(previous.text) <= 100:
        return False
    (page, pb), (next_page, cb) = ends[-1], opens[0]

    def overlaps(a, b):
        return _x_overlap(a, b) > 0.5 * min(a[2] - a[0], b[2] - b[0])

    ends_column = not any(
        owner != previous.id and overlaps(box, pb) and box[1] >= pb[3] - 2_000
        and _same_type(size, previous.font_size)
        for owner, box, size in flow[page]
    )
    opens_column = not any(
        owner != block.id and overlaps(box, cb) and box[3] <= cb[1] + 2_000
        and _same_type(size, block.font_size)
        for owner, box, size in flow[next_page]
    )
    # A page holding only floats (a full-page figure) may sit between the two parts.
    later = (next_page == page and cb[0] >= pb[2] - 2_000) or (
        next_page > page and not any(flow.get(p) for p in range(page + 1, next_page))
    )
    return bool(
        ends_column and opens_column and later
        and _same_type(previous.font_size, block.font_size)
        and re.match(r"^[(\[]?[a-z0-9]", block.text)
        and not block.marker and not _LIST_START.match(block.text)
        and not _has(block, "reference-entry")
        and not _TERMINAL.search(previous.text.rstrip())
    )


# Title -----------------------------------------------------------------------------


def _meaningful(value: str) -> bool:
    words = re.findall(r"[^\W\d_]{2,}", value)
    lowered = value.casefold()
    return len(words) >= 3 and not any(
        token in lowered for token in (".pdf", ".doc", ".tex", "microsoft word", "untitled")
    )


def _find_title(doc: _Doc) -> list[str]:
    """The title's block ids: the layout's title, the declared title, or the largest type."""

    def eligible(block):
        text = block.text.strip()
        return (
            block.label in {"title", "section_header", "text"} and 3 <= len(text) <= 400
            and re.search(r"[^\W\d_]{2}", text) is not None
            and not _has(block, "repeats:", "page-number", "stray-glyphs", "margin-column",
                         "caption:", "dates", "email", "link-line", "run-in:", "heading:",
                         "reference-entry", "on-picture:", "institution")
        )

    candidates = [
        b for b in doc.blocks if b.page in (doc.first_page, doc.first_page + 1) and eligible(b)
    ]
    if not candidates:
        return []
    start = next((b for b in candidates if b.label == "title"), None)
    for value in (c.get("value", "") for c in doc.evidence.metadata.get("title", [])):
        if start is not None or not _meaningful(value):
            continue
        wanted = _key(value)
        for index, block in enumerate(candidates):
            joined = _key(" ".join(b.text for b in candidates[index:index + 3]))
            key = _key(block.text)
            if key and (
                key == wanted or (wanted.startswith(key) and joined.startswith(wanted))
                or SequenceMatcher(None, key, wanted, autojunk=False).ratio() >= 0.9
            ):
                start = block
                break
    if start is None:
        first = [b for b in candidates if b.page == doc.first_page and b.font_size]
        if first:
            size = max(b.font_size for b in first)
            if not doc.body or size >= doc.body * 1.1:
                start = next(b for b in first if b.font_size == size)
    if start is None:
        start = next(
            (b for b in candidates if b.page == doc.first_page and b.label in HEADING_LABELS),
            None,
        )
    if start is None:
        return []
    title = [start]
    for block in candidates[candidates.index(start) + 1:]:
        last = title[-1]
        if (
            len(title) >= 3 or block.page != start.page or not block.box or not last.box
            or block.label not in {"title", "section_header", "text"}
            or (start.font_size and abs((block.font_size or 0) - start.font_size) > 0.6)
            or not -5_000 <= block.box[1] - last.box[3] <= 60_000
            or _x_overlap(block.box, last.box) <= 0
        ):
            break
        title.append(block)
    return [block.id for block in title]


# Figures and tables ----------------------------------------------------------------


@dataclass
class _Float:
    kind: str
    number: str | None
    captions: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # figure labels or table footnotes
    anchor: str | None = None
    captioned: bool = True
    id: str | None = None

    def members(self) -> list[str]:
        return [*self.captions, *self.images, *self.extra]


def _decorative(picture: Block, captioned: bool = False) -> bool:
    """Logos, badges and rules: tiny, a small image at the page edge, or repeated.

    A caption can still claim a large picture that sits where another page has one.
    """
    area = _area(picture.box)
    repeated = _has(picture, "repeats-image") and (not captioned or area < 0.05 * PAGE_AREA)
    return (
        repeated or area < 0.005 * PAGE_AREA
        or (area < 0.02 * PAGE_AREA and (_has(picture, "edge:") or picture.box[3] < 150_000))
    )


def _badge_label(picture: Block, block: Block) -> bool:
    """A short line printed right after a small icon, on its line: the badge's words.

    Icons after a name (identifier badges) sit to the right of the text, not before it.
    """
    if not block.box or block.label not in TEXTUAL or len(block.text.strip()) > 40:
        return False
    gap = block.box[0] - picture.box[2]
    overlap = min(block.box[3], picture.box[3]) - max(block.box[1], picture.box[1])
    return (
        picture.page == block.page and 0 <= gap <= 10_000
        and overlap >= 0.5 * (block.box[3] - block.box[1])
    )


def _boundary(doc: _Doc, block: Block, number: str | None = None) -> bool:
    """Text that ends a float's region: body prose, tables, other captions, body headings."""
    caption = _clue(block, "caption:")
    return (
        (doc.prose(block) and not _has(block, "on-picture:"))
        or block.label == "table"
        or (caption is not None and caption.split(":")[1] != number)
        or (block.label in HEADING_LABELS and not doc.small(block) and bool(block.font_size))
    )


def _caption_parts(doc: _Doc, caption: Block, taken: set[str]) -> list[Block]:
    """Continuation of a caption below it or in the next column, in the caption's type."""
    parts = [caption]
    bottom = caption.box[3] if caption.box else 0
    start = doc.blocks.index(caption)
    small_caption = doc.small(caption) or caption.label == "caption"
    for block in doc.blocks[start + 1:]:
        if _same_item(parts[-1], block):
            # The parser read it as the same caption, wherever the region sits.
            if not (
                doc.furniture(block) or block.id in taken
                or _has(block, "link-line", "stray-glyphs", "margin-column", "caption:")
            ):
                parts.append(block)
            if block.page == caption.page and block.box:
                bottom = max(bottom, block.box[3])
            continue
        if block.page != caption.page or not block.box or not caption.box:
            break
        if doc.furniture(block) or _has(block, "link-line", "stray-glyphs") or block.id in taken:
            bottom = max(bottom, block.box[3]) if _has(block, "link-line") else bottom
            continue
        if block.box[3] <= caption.box[1] + 5_000:
            continue  # text printed in the figure above its caption
        same_size = (
            caption.font_size and block.font_size
            and abs(caption.font_size - block.font_size) <= 0.3
        )
        below = (
            small_caption and -5_000 <= block.box[1] - bottom <= 30_000
            and abs(block.box[0] - caption.box[0]) <= 15_000
        )
        beside = (
            abs(block.box[1] - caption.box[1]) <= 10_000
            and block.box[0] >= caption.box[2] - 10_000
        )
        # A caption in body type is followed by body text in the same type.
        running = not doc.small(caption) and (doc.prose(block) or block.label in HEADING_LABELS)
        if (
            block.label in {"text", "caption", "section_header"} and len(block.text) >= 10
            and same_size and (below or beside) and not running
            and not _has(block, "caption:", "reference-entry", "heading:")
        ):
            parts.append(block)
            bottom = max(bottom, block.box[3])
            continue
        break
    return parts


def _caption_overflow(doc: _Doc, last: Block, taken: set[str]) -> list[Block]:
    """A caption cut by the page end goes on at the top of the next page, in its type."""
    text = last.text.rstrip()
    if not last.box or not isinstance(last.page, int) or not (
        _RUNS_ON.search(text) or (last.box[3] > 850_000 and not _TERMINAL.search(text))
    ):
        return []
    # The next page in reading order; the layout may have listed a region of it earlier.
    for block in doc.by_page.get(last.page + 1, []):
        if block.id in taken:
            continue
        if doc.furniture(block) or block.label not in TEXTUAL or _has(
            block, "stray-glyphs", "link-line", "margin-column"
        ):
            continue
        if (
            block.label in {"text", "caption"} and block.box and block.box[1] < 300_000
            and last.font_size and block.font_size
            and abs(last.font_size - block.font_size) <= 0.3 and doc.small(block)
            # A sentence goes on in lower case, unless the caption said it continues.
            and (re.match(r"^[(\[]?[a-z0-9]", block.text) or _RUNS_ON.search(text))
            and not _has(block, "caption:", "continues:")
        ):
            return _caption_parts(doc, block, taken)
        return []
    return []


def _candidates(doc: _Doc, page: int, kinds: set[str], used: set[str]) -> list[Block]:
    return [
        b for b in doc.by_page.get(page, [])
        if b.label in kinds and b.id not in used
        and not (b.label == "picture" and _decorative(b, captioned=True))
    ]


def _nearest(doc: _Doc, frame, visuals: list[Block], number: str | None, above: bool):
    """Visuals directly above (or below) a caption, with no boundary text between."""
    page_blocks = doc.by_page.get(visuals[0].page, []) if visuals else []

    def gap(visual):
        return frame[1] - visual.box[3] if above else visual.box[1] - frame[3]

    def blocked(visual):
        low, high = (visual.box[3], frame[1]) if above else (frame[3], visual.box[1])
        return any(
            _boundary(doc, other, number) and other.box and other is not visual
            and other.box[1] >= low - 5_000 and other.box[3] <= high + 5_000
            and _x_overlap(other.box, frame) > 0
            for other in page_blocks
        )

    chosen = [
        v for v in visuals
        if _x_overlap(v.box, frame) > 0 and gap(v) >= -25_000 and gap(v) <= 600_000
        and (v.box[1] < frame[1] if above else v.box[3] > frame[3]) and not blocked(v)
    ]
    if not chosen:
        return []
    first = min(chosen, key=gap)
    band = [first.box[1], first.box[3]]
    group = [first]
    for visual in sorted(chosen, key=gap):
        if visual is first:
            continue
        # Panels in a row, or stacked directly against the nearest one.
        if visual.box[3] >= band[0] - 30_000 and visual.box[1] <= band[1] + 30_000:
            group.append(visual)
            band = [min(band[0], visual.box[1]), max(band[1], visual.box[3])]
    return group


def _claim(doc: _Doc, group: _Float, parts: list[Block], used: set[str]) -> list[str]:
    """The visuals a numbered caption names: declared links, then geometry, then a plate."""
    refs = {part.extra.get("documentRef") for part in parts}
    # The layout's own link may show a figure as a table grid; the caption decides the kind.
    declared = [
        b for b in doc.blocks
        if b.label in {"picture", "table"} and b.id not in used
        and refs & set(b.extra.get("captionRefs", []))
    ]
    caption = parts[0]
    frame = _union([doc.union(p) for p in parts if p.page == caption.page])
    if declared:
        found = [b.id for b in declared]
        seed = declared[0]
        if frame and seed.label == "picture" and seed.page == caption.page and seed.box:
            # The layout links one panel; the panels beside it belong to the same figure.
            visuals = _candidates(doc, caption.page, {"picture"}, used)
            row = _nearest(doc, frame, visuals, group.number, seed.box[1] < frame[1])
            if seed in row:
                found += [b.id for b in row if b.id not in found]
        return sorted(found, key=doc.position.__getitem__)
    if not caption.box or not frame:
        return []
    order = [("table", False), ("table", True), ("picture", False), ("picture", True)]
    if group.kind == "figure":
        order = [("picture", True), ("picture", False)]
    for kind, above in order:
        visuals = _candidates(doc, caption.page, {kind}, used)
        found = _nearest(doc, frame, visuals, group.number, above)
        if found:
            return [b.id for b in found]
    if group.kind != "figure" or not isinstance(caption.page, int):
        return []
    # A full-page plate can have its caption on the adjoining page.
    page = None
    if frame[1] > 600_000:
        page = caption.page + 1
    elif frame[1] < 200_000:
        page = caption.page - 1
    plates = [
        b for b in _candidates(doc, page, {"picture"}, used)
        if _area(b.box) > 0.15 * PAGE_AREA and not any(
            (other := doc.by_ref.get(ref)) is not None and _has(other, "caption:")
            for ref in b.extra.get("captionRefs", [])
        )
    ] if page else []
    return [plates[0].id] if len(plates) == 1 else []


def _figure_labels(doc: _Doc, group: _Float, used: set[str]) -> list[str]:
    """Text printed on or around a figure's images: panel letters, axis titles, legends."""
    images = [doc.by_id[i] for i in group.images]
    captions = [doc.by_id[i] for i in group.captions]
    labels = []
    for image in images:
        blocks = doc.by_page.get(image.page, [])
        region = None
        same_page = [c for c in captions if c.page == image.page and c.box]
        # A page without running text is the figure's: its short lines are labels.
        figure_page = not any(
            doc.prose(b) and not _has(b, "caption:", "on-picture:") for b in blocks
        )
        below = min((c.box[1] for c in same_page), default=1_000_000)
        if same_page:
            frame = _union([image.box, *(c.box for c in same_page)])
            caption_top = min(c.box[1] for c in same_page)
            if caption_top >= image.box[3] - 25_000:
                above = [
                    b.box[3] for b in blocks
                    if b.box[3] <= image.box[1] + 5_000 and _boundary(doc, b, group.number)
                    and _x_overlap(b.box, frame) > 0 and b.id not in group.members()
                ]
                region = (frame[0] - 10_000, max(above, default=0), frame[2] + 10_000, caption_top)
                if any(
                    _boundary(doc, b, group.number) and _within(b.box, region)
                    and b.id not in group.members() for b in blocks
                ):
                    region = None
        for block in blocks:
            if (
                block.id in used or block.id in labels or block.label not in TEXTUAL
                or doc.furniture(block) or _has(block, "caption:", "reference-entry")
                or (doc.prose(block) and not doc.small(block))
            ):
                continue
            letter = re.fullmatch(r"\(?[A-Za-z]\)?", block.text.strip()) is not None
            loose = doc.small(block) or letter or not block.font_size or _has(block, "stray-glyphs")
            # A short unnumbered title read straight before the image is the figure's.
            titled = (
                len(block.text.strip()) <= 80 and doc.next_placeable.get(block.id) in group.images
                and 0 <= image.box[1] - block.box[3] <= 30_000
                and _x_overlap(block.box, image.box) > 0
            )
            pictured = (titled or figure_page) and (
                len(block.text.strip()) <= 80 and block.box[1] < below
                and not _NUMBERING.match(block.text) and not _has(block, "heading:")
                and _x_overlap(block.box, image.box) > 0
                and _within(block.box, _pad(image.box, 100_000))
            )
            if block.label in HEADING_LABELS and not loose and not pictured:
                continue  # a heading in body type is never part of a figure
            if pictured or (
                (_within(block.box, image.box) and (loose or len(block.text) <= 200))
                or (_within(block.box, _pad(image.box, 12_000)) and loose)
                or (region and _within(block.box, region) and not _boundary(doc, block))
            ):
                labels.append(block.id)
    return labels


def _table_footnotes(doc: _Doc, group: _Float, used: set[str]) -> list[str]:
    """Small type directly under a table: abbreviations, significance marks, sources."""
    notes = []
    for table in (doc.by_id[i] for i in group.images):
        refs = set(table.extra.get("footnoteRefs", []))
        notes += [
            b.id for b in doc.blocks if b.extra.get("documentRef") in refs and b.id not in used
        ]
        bottom = table.box[3] if table.box else 0
        index = doc.blocks.index(table)
        for block in doc.blocks[index + 1:]:
            if block.page != table.page or not block.box or not table.box:
                break
            if block.id in used or doc.furniture(block) or block.id in group.captions:
                continue
            if _has(block, "link-line"):
                bottom = max(bottom, block.box[3])
                continue
            if (
                block.label in {"text", "footnote", "caption"} and block.id not in notes
                and (doc.small(block) or block.label == "footnote") and not doc.prose(block, 400)
                and -5_000 <= block.box[1] - bottom <= 30_000
                and block.box[0] >= table.box[0] - 10_000 and block.box[2] <= table.box[2] + 10_000
                and not _has(block, "caption:")
            ):
                notes.append(block.id)
                bottom = block.box[3]
                continue
            break
    return notes


def _match_floats(doc: _Doc) -> list[_Float]:
    """Caption-first figures and tables; continued captions join their float."""
    used: set[str] = set()
    floats: list[_Float] = []
    numbered: dict[tuple[str, str], _Float] = {}
    for block in doc.blocks:
        label = _clue(block, "caption:")
        if label is None or block.id in used or doc.furniture(block) or _has(
            block, "margin-column"
        ):
            continue
        kind, number = label.split(":", 1)
        parts = _caption_parts(doc, block, used)
        used.update(p.id for p in parts)
        overflow = _caption_overflow(doc, parts[-1], used)
        used.update(p.id for p in overflow)
        group = numbered.get((kind, number))
        fresh = group is None
        if fresh:
            group = _Float(kind, number, anchor=block.id)
        group.captions += [p.id for p in [*parts, *overflow]]
        images = _claim(doc, group, parts, used)
        used.update(images)
        group.images += images
        if fresh:
            numbered[(kind, number)] = group
            floats.append(group)
    # Captions the layout linked to a visual without a printed number.
    for visual in [b for b in doc.blocks if b.label in {"picture", "table"} and b.id not in used]:
        captions = [
            doc.by_ref[ref] for ref in visual.extra.get("captionRefs", [])
            if ref in doc.by_ref and doc.by_ref[ref].placeable and doc.by_ref[ref].id not in used
            and not _has(doc.by_ref[ref], "caption:")
        ]
        if captions:
            kind = "table" if visual.label == "table" else "figure"
            group = _Float(kind, None, [c.id for c in captions], [visual.id], anchor=captions[0].id)
            used.update(group.members())
            floats.append(group)
    for group in floats:
        if group.kind == "figure":
            group.extra = _figure_labels(doc, group, used)
        else:
            group.extra = _table_footnotes(doc, group, used)
        used.update(group.extra)
    return [group for group in floats if group.images]


# Draft -------------------------------------------------------------------------------


def _names_line(text: str, authors: list[str]) -> bool:
    """A line of personal names: declared authors, or capitalised names with separators."""
    if (
        not 3 <= len(text) <= 1200 or _EMAIL.search(text) or _INSTITUTION.search(text)
        or re.match(r"\W*\d{1,2}\s*[A-Z]", text)  # a numbered affiliation
    ):
        return False
    if authors:
        key = re.sub(r"\d", "", _key(text))
        rest = key
        for name in sorted(authors, key=len, reverse=True):
            rest = rest.replace(name, "")
        if len(key) > 6 and 1 - len(rest) / len(key) >= 0.6:
            return True
    words = re.findall(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", text)
    if not words or re.search(r"[a-z]{3,}\.\s+[A-Z][a-z]", text):
        return False
    lower = [w for w in words if w[0].islower() and w.casefold() not in _PARTICLES]
    if len(lower) > max(2, len(words) // 6):
        return False
    return bool(re.search(r",|\band\b|&|·", text)) or 2 <= len(words) <= 4


_AUTHOR_CELL = re.compile(
    r"^\W{0,2}(?P<name>(?:[^\W\d_][\w'’\-]*\.?\s+){1,3}[^\W\d_][\w'’\-]*\.?)"
    r"\s*(?:[∗*†‡§¶#✉]|\d{1,2}(?:\s*,\s*\d{1,2})*(?=\W))"
)


def _author_cell(text: str, authors: list[str]) -> bool:
    """One author's cell of a grid: a name with its marker (or a declared author's
    name), then the affiliation or e-mail printed under it in the same block."""
    text = text.strip()
    if len(text) > 300 or not (_EMAIL.search(text) or _INSTITUTION.search(text)):
        return False
    words = text.split()[:4]
    if any(_key(" ".join(words[:n])) in authors for n in range(2, len(words) + 1)):
        return True
    match = _AUTHOR_CELL.match(text)
    return match is not None and not _INSTITUTION.search(match["name"]) and all(
        word[0].isupper() or word.casefold() in _PARTICLES for word in match["name"].split()
    )


def _affiliation_like(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text)
    lower = [w for w in words if w[0].islower() and w.casefold() not in _PARTICLES]
    return bool(_INSTITUTION.search(text)) and len(lower) <= 0.3 * max(1, len(words))


@dataclass
class _Box:
    """A section, back part or dropped scope while the plan is being built."""

    where: str  # body | back | drop
    part: str | None = None
    heading: str | None = None
    level: int = 1
    items: list[str] = field(default_factory=list)
    reason: str = ""


class _Draft:
    def __init__(self, evidence: Evidence):
        self.doc = _Doc(evidence)
        self.where: dict[str, str] = {}
        self.drops: dict[str, str] = {}
        self.title: list[str] = []
        self.authors: list[str] = []
        self.front: list[Part] = []
        self.notes: list[Note] = []
        self.floats: list[_Float] = []
        self.anchors: dict[str, _Float] = {}
        self.availability_notes: list[str] = []
        self.counts = Counter()
        self.names = []  # declared author names, as keys
        for value in (c.get("value", "") for c in evidence.metadata.get("authors", [])):
            parts = value.split(";") if ";" in value else value.split(",")
            self.names += [_key(part) for part in parts if len(_key(part)) > 3]

    def take(self, block_id: str, where: str) -> bool:
        if block_id in self.where:
            return False
        self.where[block_id] = where
        return True

    def drop(self, block_id: str, reason: str) -> bool:
        if self.take(block_id, "drop"):
            self.drops[block_id] = reason
            return True
        return False

    def free(self, block: Block) -> bool:
        return block.id not in self.where

    # The passes, in order of authority.

    def plan(self) -> Plan:
        self.type = self._document_type()
        self._furniture()
        self._figures_and_tables()
        self._title()
        self._small_matter()
        self._authors()
        self._front()
        self._notes()
        boxes = self._sections()
        joins = self._joins(boxes)
        return self._build(boxes, joins)

    def _document_type(self) -> str:
        doc = self.doc
        bibliography = any(_has(b, "heading:references") for b in doc.blocks) or sum(
            _has(b, "reference-entry") for b in doc.blocks
        ) >= 5
        early = [b for b in doc.blocks if isinstance(b.page, int) and b.page <= doc.first_page + 2]
        abstract = any(
            _has(b, "heading:abstract", "heading:summary", "run-in:abstract") for b in early
        )
        if not abstract:
            title = _find_title(doc)
            if title:
                start, page = doc.position[title[-1]], doc.by_id[title[-1]].page
                abstract = any(
                    b.page == page and doc.position[b.id] > start and b.label == "text"
                    and len(b.text) >= 300 and not doc.small(b) for b in doc.blocks
                )
        if bibliography and abstract:
            return "paper"
        sizes = list(doc.evidence.pages.values())
        landscape = sum(1 for s in sizes if s["width"] > s["height"] * 1.05)
        if landscape >= max(2, 0.6 * len(sizes)) and (not doc.body or doc.body >= 14):
            return "slides"
        return "unknown"

    def _furniture(self) -> None:
        for block in self.doc.blocks:
            if self.doc.furniture(block):
                self.drop(block.id, "page furniture")

    def _figures_and_tables(self) -> None:
        doc, paper = self.doc, self.type == "paper"
        for group in _match_floats(doc):
            members = [m for m in group.members() if self.free(doc.by_id[m])]
            if not set(group.images) <= set(members):
                continue
            group.captions = [c for c in group.captions if c in members]
            group.extra = [e for e in group.extra if e in members]
            group.anchor = group.captions[0] if group.captions else group.images[0]
            self._keep(group)
        for picture in doc.pictures:
            if not self.free(picture):
                continue
            large = _area(picture.box) >= 0.15 * PAGE_AREA
            if (
                _decorative(picture) or _has(picture, "edge:", "margin-column")
                or picture.box[3] < 150_000 or (paper and not large)
            ):
                self.drop(picture.id, "decoration")
                for block in doc.by_page.get(picture.page, []):
                    if not doc.prose(block) and (
                        _clue(block, "on-picture:") == picture.id or _badge_label(picture, block)
                    ):
                        self.drop(block.id, "decoration")
                continue
            group = _Float("figure", None, images=[picture.id], anchor=picture.id, captioned=False)
            group.extra = _figure_labels(doc, group, set(self.where))
            self._keep(group)

    def _keep(self, group: _Float) -> None:
        for member in group.members():
            self.take(member, "float")
        self.floats.append(group)
        self.anchors[group.anchor] = group

    def _title(self) -> None:
        self.title = [i for i in _find_title(self.doc) if self.take(i, "title")]

    def _small_matter(self) -> None:
        """Stray glyphs, margin columns, dates, identifiers, affiliations and page notes."""
        doc, paper = self.doc, self.type == "paper"
        title_page = doc.by_id[self.title[0]].page if self.title else doc.first_page
        headed = any(_has(b, "heading:availability") for b in doc.blocks)
        previous = None
        for block in doc.blocks:
            if doc.furniture(block):
                continue
            before, previous = previous, block
            if not self.free(block) or block.label not in TEXTUAL:
                continue
            opening = block.page in {doc.first_page, title_page}
            text = block.text
            # A block running on to another page is not a note, whatever it starts with.
            one_page = len({region.get("page") for region in block.regions}) <= 1
            following = doc.by_id.get(doc.next_placeable.get(block.id, ""))
            runs_on = (
                following is not None and _same_item(block, following)
                and not _TERMINAL.search(text.rstrip())
            )
            if _has(block, "stray-glyphs"):
                self.drop(block.id, "stray glyphs")
            elif paper and _has(block, "run-in:availability") and not headed:
                # Wherever the layout set it (a side box, mid-page), it is end matter.
                self.take(block.id, "availability")
                self.availability_notes += [block.id, *self._rest_of_item(block, "availability")]
            elif paper and _has(block, "margin-column"):
                self.drop(block.id, "margin column")
            elif paper and opening and _has(block, "licence") and len(text) <= 1500:
                self.drop(block.id, "licence")
            elif _has(block, "dates") and (paper or opening):
                self.drop(block.id, "dates")
            elif _has(block, "link-line") and (
                paper and ("doi" in text.casefold() or doc.small(block) or opening)
            ):
                self.drop(block.id, "identifier line")
            elif opening and _author_cell(text, self.names):
                continue  # an author's grid cell; the authors pass takes it
            elif paper and one_page and _has(block, "email") and len(text) < 400:
                self.drop(block.id, "e-mail")
            elif (
                paper and one_page and opening and len(text) < 3000
                and not _names_line(text, [])
                and (_affiliation_like(text) or self._more_affiliations(block, before))
            ):
                self.drop(block.id, "affiliations")
            elif paper and _has(block, "run-in:back-matter", "run-in:acknowledgements"):
                self.drop(block.id, "back matter")
            elif paper and one_page and not runs_on and (
                block.label == "footnote" or _has(block, "note-marker")
                or (opening and _has(block, "page-foot"))
            ):
                self.drop(block.id, "page note")
            elif _CONTINUED.match(text) and len(text) < 60:
                self.drop(block.id, "continuation marker")
            elif paper and _has(block, "before-title") and len(text) < 80:
                self.drop(block.id, "masthead")

    def _more_affiliations(self, block: Block, before: Block | None) -> bool:
        """The next numbered line of an affiliation list, even without an institution word."""
        return bool(
            before is not None and self.drops.get(before.id) == "affiliations"
            and before.page == block.page and before.font_size == block.font_size
            and (re.match(r"\W*\d{1,2}\s*[A-Z]", block.text) or any(
                s.get("start") == 0 and s.get("script") == "super" for s in block.inline
            ))
        )

    def _authors(self) -> None:
        """Name lines under the title, or the cells of an author grid (name, then
        affiliation and e-mail in one block)."""
        doc, names = self.doc, self.names
        if not self.title:
            return
        last = doc.by_id[self.title[-1]]
        start = doc.blocks.index(last)
        for block in doc.blocks[start + 1:]:
            if block.page != last.page or len(self.authors) >= 16:
                break
            if not self.free(block) or block.label not in TEXTUAL:
                continue
            author = _names_line(block.text, names) or _author_cell(block.text, names)
            if block.label in HEADING_LABELS and not author:
                break
            if author and not _has(block, "run-in:", "heading:"):
                self.take(block.id, "authors")
                self.authors.append(block.id)
                continue
            break
        for block in doc.by_page.get(last.page, []):
            if self.free(block) and block.label in TEXTUAL and _author_cell(block.text, names):
                self.drop(block.id, "affiliations")  # a grid cell away from the author block

    def _front(self) -> None:
        doc = self.doc
        early = [
            b for b in doc.blocks if isinstance(b.page, int) and b.page <= doc.first_page + 2
        ]
        body_started = False
        has_abstract = any(_has(b, "heading:abstract") for b in early)
        for block in early:
            if not self.free(block):
                continue
            kind = _clue(block, "heading:")
            heading = block.label in HEADING_LABELS or kind is not None
            if heading and kind in {"abstract", "summary", "keywords"} and not body_started:
                part = kind
                if kind == "summary" and not has_abstract and not self._front_has("abstract"):
                    part = "abstract"
                self.take(block.id, "front")
                blocks = self._front_blocks(block)
                self.front.append(Part(part=part, heading=block.id, blocks=blocks))
                continue
            if heading and block.label in HEADING_LABELS:
                body_started = True
            if _has(block, "run-in:keywords"):
                self.take(block.id, "front")
                self.front.append(Part(part="keywords", blocks=[block.id]))
            elif _has(block, "run-in:abstract") and not body_started and not self._front_has(
                "abstract"
            ):
                self.take(block.id, "front")
                blocks = [block.id] + self._front_blocks(block, same=block)
                self.front.append(Part(part="abstract", blocks=blocks))
        if not self._front_has("abstract") and self.title and self.type == "paper":
            last = doc.by_id[self.title[-1]]
            for block in doc.blocks[doc.blocks.index(last) + 1:]:
                if block.page != last.page:
                    break
                if not self.free(block):
                    continue
                if block.label in HEADING_LABELS:
                    break
                if block.label == "text" and len(block.text) >= 300 and not doc.small(block):
                    self.take(block.id, "front")
                    blocks = [block.id] + self._rest_of_item(block)
                    self.front.append(Part(part="abstract", blocks=blocks))
                    break
        self.front.sort(key=lambda part: doc.position[part.heading or part.blocks[0]])

    def _front_has(self, name: str) -> bool:
        return any(part.part == name for part in self.front)

    def _rest_of_item(self, block: Block, where: str = "front") -> list[str]:
        """The free regions that go on with ``block``'s item as one paragraph, taken along."""
        rest, last = [], block
        for other in self.doc.blocks[self.doc.blocks.index(block) + 1:]:
            if not _same_item(block, other):
                break
            if not self.free(other):
                continue
            if other.label not in TEXTUAL or _new_paragraph(last, other):
                break
            self.take(other.id, where)
            rest.append(other.id)
            last = other
        return rest

    def _front_blocks(self, opening: Block, same: Block | None = None) -> list[str]:
        """Text following a front heading (or a run-in label) until the next heading."""
        doc, blocks = self.doc, []
        last = opening
        for block in doc.blocks[doc.blocks.index(opening) + 1:]:
            if not isinstance(block.page, int) or block.page > (opening.page or 0) + 1:
                break
            if not self.free(block):
                continue
            if block.label not in {"text", "list_item"} or _has(block, "heading:", "caption:"):
                break
            if same is not None and not _same_item(last, block) and (
                block.page != same.page or block.font_size != same.font_size
            ):
                break
            self.take(block.id, "front")
            blocks.append(block.id)
            last = block
        return blocks

    def _notes(self) -> None:
        if self.type == "paper":
            return
        for block in self.doc.blocks:
            if self.notes and self.free(block) and _same_item(
                self.doc.by_id[self.notes[-1].blocks[-1]], block
            ):
                self.take(block.id, "note")
                self.notes[-1].blocks.append(block.id)
                continue
            if self.free(block) and (block.label == "footnote" or _has(block, "note-marker")):
                marker = next(
                    (block.text[s["start"]:s["end"]] for s in block.inline
                     if s.get("start") == 0 and s.get("script") == "super"),
                    None,
                )
                if marker is None and (found := re.match(r"\s*([*†‡§¶#]+|\d{1,3})", block.text)):
                    marker = found[1]
                self.take(block.id, "note")
                number = len(self.notes) + 1
                self.notes.append(Note(id=f"N{number}", marker=marker, blocks=[block.id]))

    # Sections, back parts and placement of floats.

    def _levels(self, headings: list[Block], appendix: set[str]) -> dict[str, int]:
        """Numbering first (roman above arabic), then type size, then the layout's level."""
        roman = any((m := _NUMBERING.match(h.text)) and m["roman"] for h in headings)
        sized = defaultdict(set)
        numbered_sizes = set()
        for h in headings:
            sized[_half(h.font_size)].add(h.level or 0)
            m = _NUMBERING.match(h.text)
            if m and (m["arabic"] or m["roman"]):
                numbered_sizes.add(_half(h.font_size))

        def key(size: float, level: int) -> tuple[float, int]:
            # The layout's level only splits one size when numbering does not.
            split = size not in numbered_sizes and len(sized[size]) > 1
            return -size, level if split else 0

        keys = sorted({key(size, lv) for size, found in sized.items() if size for lv in found})
        rank = {value: index + 1 for index, value in enumerate(keys)}
        unsized = sorted({h.level or 0 for h in headings if not h.font_size})
        levels = {}
        for h in headings:
            m = _NUMBERING.match(h.text)
            size = _half(h.font_size)
            if mark := _clue(h, "bookmark:"):
                level = int(mark)
            elif m and m["arabic"]:
                level = m["arabic"].count(".") + 1 + (1 if roman else 0)
            elif m and m["roman"]:
                level = 1
            elif m and m["letter"] and (h.id in appendix or m["dot"] or m["sub"]):
                level = 1 + (m["sub"].count(".") + 1 if m["sub"] else 0)
            elif size:
                level = rank.get(key(size, h.level or 0), 1)
            else:
                level = unsized.index(h.level or 0) + 1 if unsized else 1
            levels[h.id] = max(1, min(6, level))
        return levels

    def _nest(self, headings: list[Block], levels: dict[str, int]) -> None:
        """Headings set like Results but printed right under it are its subsections."""
        evidence = self.doc.evidence
        canonical = {h.id for h in headings if _IMRAD.fullmatch(_section_name(h.text))}
        nesting = set()
        for first, second in zip(headings, headings[1:], strict=False):
            start, stop = evidence.position(first.id), evidence.position(second.id)
            empty = not any(
                b.placeable and self.free(b) for b in evidence.blocks[start + 1:stop]
            )
            if (
                empty and first.id in canonical and second.id not in canonical
                and levels[first.id] == levels[second.id] and not _has(second, "heading:")
            ):
                nesting.add(levels[first.id])
        for level in nesting:
            inside = False
            for heading in headings:
                if levels[heading.id] < level:
                    inside = False
                elif levels[heading.id] == level and (
                    heading.id in canonical or _has(heading, "heading:")
                ):
                    inside = heading.id in canonical
                elif inside:
                    levels[heading.id] = min(6, levels[heading.id] + 1)

    def _is_heading(self, block: Block) -> bool:
        return block.label in HEADING_LABELS or (
            _has(block, "heading:", "bookmark:") and block.label in {"text", "other"}
            and len(block.text) <= (200 if _has(block, "bookmark:") else 80)
        )

    def _sections(self) -> list[_Box]:
        doc, paper = self.doc, self.type == "paper"
        free = [b for b in doc.blocks if self.free(b) or b.id in self.anchors]
        headings = [b for b in free if self.free(b) and self._is_heading(b)]
        first_refs = next(
            (doc.position[h.id] for h in headings if _clue(h, "heading:") == "references"), None
        )
        after = {
            h.id for h in headings
            if first_refs is not None and doc.position[h.id] > first_refs
        }
        levels = self._levels(headings, after)
        if paper:
            self._nest(headings, levels)
        boxes: list[_Box] = []
        closing = False  # a level-1 back part has begun
        stack: list[tuple[int, str, str]] = []
        current: _Box | None = None
        references: _Box | None = None
        rehomed: list[_Float] = []
        page = None

        def open_box(**kwargs) -> _Box:
            box = _Box(**kwargs)
            boxes.append(box)
            return box

        for block in free:
            if block.id not in self.anchors and not self.free(block):
                continue  # claimed on the way, e.g. as a table's footnote
            if self.type == "slides" and block.page != page and block.label not in FURNITURE:
                page = block.page
                current = open_box(where="body")
            if block.id in self.anchors:
                group = self.anchors[block.id]
                if current is None:
                    current = open_box(where="body")
                if current.where == "drop" and not group.captioned:
                    for member in group.members():
                        self.where.pop(member, None)
                        self.drop(member, current.reason)
                elif current.where == "drop" or current is references:
                    rehomed.append(group)  # a figure is never end matter or a reference
                else:
                    current.items.append(self._float_id(group))
                continue
            if self._is_heading(block):
                kind = _clue(block, "heading:")
                level = levels[block.id]
                while stack and stack[-1][0] >= level:
                    stack.pop()
                inherited, scope = (stack[-1][1], stack[-1][2]) if stack else ("body", "")
                if kind in {None, "abstract", "summary", "keywords"}:
                    effective = inherited if inherited != "references" else "body"
                    if effective == "body" and paper and block.id in after:
                        effective = "appendix"
                    elif effective == "body" and paper and closing and level == 1:
                        effective = "other"  # e.g. an ethics statement among the end matter
                elif kind in {"back-matter", "acknowledgements"}:
                    effective = "drop" if paper else (
                        "acknowledgements" if kind == "acknowledgements" else "other"
                    )
                elif kind == "supplementary":
                    effective = "other"
                else:
                    effective = kind
                kept = {"references", "availability", "appendix"}
                if inherited == "drop" and effective not in kept:
                    effective = "drop"
                reason = scope if effective == "drop" and inherited == "drop" else (
                    "back matter: " + _section_name(block.text)[:40]
                )
                closing = closing or (level == 1 and effective != "body")
                stack.append((level, effective, reason))
                if self.type == "slides" and current is not None and not current.heading and not (
                    current.items
                ):
                    current.heading, current.level = block.id, 1
                    self.take(block.id, "heading")
                elif effective == "body":
                    current = open_box(where="body", heading=block.id, level=level)
                    self.take(block.id, "heading")
                elif effective == "references":
                    if references is None:
                        references = open_box(
                            where="back", part="references", heading=block.id, level=level
                        )
                        self.take(block.id, "heading")
                    else:
                        self.drop(block.id, "repeated references heading")
                    current = references
                elif effective == "drop":
                    self.drop(block.id, reason)
                    current = _Box(where="drop", reason=reason)
                else:
                    current = open_box(where="back", part=effective, heading=block.id, level=level)
                    self.take(block.id, "heading")
                continue
            last = references.items[-1] if references and references.items else None
            if references is not None and current is not references and (
                _has(block, "reference-entry") or (last and f"continues:{last}" in block.clues)
                or (last in doc.by_id and _same_item(doc.by_id[last], block))
            ):
                self.take(block.id, "references")
                references.items.append(block.id)
                continue
            if current is None:
                current = open_box(where="body")
            if block.label == "table" and current.where != "drop":
                group = _Float("table", None, images=[block.id], anchor=block.id, captioned=False)
                group.extra = _table_footnotes(self.doc, group, set(self.where))
                self._keep(group)
                current.items.append(self._float_id(group))
            elif current.where == "drop":
                self.drop(block.id, current.reason)
            else:
                self.take(block.id, current.where)
                current.items.append(block.id)
        rehomed.sort(key=lambda g: (doc.by_id[g.anchor].page or 0, doc.position[g.anchor]))
        if rehomed or self.availability_notes:
            if self.availability_notes:
                boxes.append(_Box(where="back", part="availability", items=self.availability_notes))
            if rehomed:
                boxes.append(_Box(
                    where="back", part="appendix", items=[self._float_id(g) for g in rehomed]
                ))
        if references is None and paper:
            self._reference_run(boxes)
        return boxes

    def _reference_run(self, boxes: list[_Box]) -> None:
        """Without a references heading, a closing run of bibliography entries is one list."""
        for box in reversed(boxes):
            if box.where != "body":
                continue
            entries = [i for i in box.items if BLOCK_ID.match(i)]
            tail = []
            for item in reversed(box.items):
                if not BLOCK_ID.match(item) or not _has(self.doc.by_id[item], "reference-entry"):
                    break
                tail.insert(0, item)
            if len(tail) >= 5 and entries:
                box.items = box.items[: len(box.items) - len(tail)]
                boxes.append(_Box(where="back", part="references", items=tail))
            return

    def _float_id(self, group: _Float) -> str:
        if group.id is None:
            prefix = "F" if group.kind == "figure" else "T"
            self.counts[prefix] += 1
            group.id = f"{prefix}{self.counts[prefix]}"
        return group.id

    def _joins(self, boxes: list[_Box]) -> list[list[str]]:
        """A block continuing its neighbour in the same part; figures may sit between.

        Regions the parser read as one item are one paragraph unless the second
        plainly starts something new.
        """
        joins, by_id = [], self.doc.by_id
        lists = [box.items for box in boxes if box.where != "drop"]
        for items in [*lists, *(part.blocks for part in self.front)]:
            previous = None
            for item in items:
                if not BLOCK_ID.match(item):
                    continue
                block = by_id[item]
                if previous and (
                    f"continues:{previous}" in block.clues
                    or (_same_item(by_id[previous], block)
                        and not _new_paragraph(by_id[previous], block))
                ):
                    joins.append([previous, item])
                previous = item
        return joins

    def _build(self, boxes: list[_Box], joins: list[list[str]]) -> Plan:
        doc = self.doc

        def ids(items):
            return _compress(items, doc)

        body = [
            Section(heading=box.heading, level=box.level, blocks=ids(box.items))
            for box in boxes if box.where == "body" and (box.heading or box.items)
        ]
        back = [
            Part(part=box.part, heading=box.heading, level=box.level, blocks=ids(box.items))
            for box in boxes if box.where == "back" and (box.heading or box.items)
        ]
        figures, tables = [], []
        for group in sorted(
            (g for g in self.floats if g.id), key=lambda g: int(g.id[1:])
        ):
            if group.kind == "figure":
                figures.append(Figure(
                    id=group.id, number=group.number, images=group.images,
                    caption=group.captions, labels=group.extra,
                ))
            else:
                tables.append(Table(
                    id=group.id, number=group.number, table=group.images,
                    caption=group.captions, footnotes=group.extra,
                ))
        reasons: dict[str, list[str]] = defaultdict(list)
        for block_id in sorted(self.drops, key=doc.position.__getitem__):
            reasons[self.drops[block_id]].append(block_id)
        front = [
            Part(part=p.part, heading=p.heading, level=p.level, blocks=ids(p.blocks))
            for p in self.front
        ]
        return Plan(
            type=self.type,
            title=self.title,
            authors=self.authors,
            front=front,
            body=body,
            back=back,
            figures=figures,
            tables=tables,
            notes=self.notes,
            joins=joins,
            drop=[Drop(blocks=ids(items), reason=reason) for reason, items in reasons.items()],
            by="rules",
        )


def _compress(items: list[str], doc: _Doc) -> list[str]:
    """Runs of three or more neighbouring placeable blocks become ranges ``bA..bB``."""
    result: list[str] = []
    run: list[str] = []

    def flush():
        if len(run) >= 3:
            result.append(f"{run[0]}..{run[-1]}")
        else:
            result.extend(run)
        run.clear()

    for item in items:
        if not BLOCK_ID.match(item):
            flush()
            result.append(item)
        elif run and doc.next_placeable.get(run[-1]) == item:
            run.append(item)
        else:
            flush()
            run.append(item)
    flush()
    return result
