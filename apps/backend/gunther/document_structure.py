"""Source-defined structure and metadata, independent of document genre.

Layout labels describe roles, not a required outline. Unheaded text stays
unheaded. Visual annotations and page furniture remain inspectable, without
being interleaved with the document's prose or embedded as body evidence.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from gunther.documents import ParsedDocument

NON_BODY = {
    "footnote",
    "page_header",
    "page_footer",
    "page_number",
    "sidebar",
    "margin_note",
    "metadata",
    "figure_text",
    "figure_panel",
    "embedded_image",
}


def evidence_eligible(kind: Any, payload: Any, *, include_headings: bool = False) -> Any:
    """One body-evidence policy for parsed blocks and stored SQLite passages.

    Pass a kind and a payload dict/JSON string for a boolean, or SQLAlchemy
    kind/payload columns for a SQL predicate. Headings may supply context to
    readers, but are not standalone evidence. ``noEmbed`` remains a separate
    vector-quality flag: short or numeric body text is still readable evidence.
    Original elements and their provenance are never removed by this policy.
    """
    excluded = NON_BODY | {"reference"}
    if not include_headings:
        excluded = excluded | {"heading"}
    if isinstance(kind, str):
        data = json.loads(payload or "{}") if isinstance(payload, str) else (payload or {})
        return kind not in excluded and not data.get("reference")

    from sqlalchemy import and_, func

    return and_(
        kind.not_in(sorted(excluded)),
        func.coalesce(func.json_extract(payload, "$.reference"), 0) == 0,
    )


DATE_LABEL = re.compile(
    r"^(received|accepted|published(?: online)?|publication date|issued|updated|"
    r"出版日期|发布日期|接收日期|修订日期)\s*[:：]\s*(.+)$",
    re.I,
)
DATE_VALUE = re.compile(r"\b(?:19|20)\d{2}\b")
CAPTION_LABEL = re.compile(
    r"^(?:(?:extended data|supplementary|supporting)\s+)?"
    r"(?:fig(?:ure)?\.?|table|图|表|abbildung|tabelle)\s*(?:[A-Z]?\d+|[IVX]+)\b", re.I
)
PUBLICATION_NOTICE = re.compile(
    r"^(?:©|copyright\b|(?:publisher|editor)(?:['’]s)?\s+note\b|"
    r"open access\b|licen[cs]e\b|版权|许可声明|出版者说明)",
    re.I,
)


def normalized(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def _clean(value: object) -> str:
    text = str(value or "")
    # Some PDF generators escape XML numeric entities a second time.
    text = re.sub(r"amp#(x[0-9a-f]+|\d+);", r"&#\1;", text, flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:2000]


def _field(metadata: dict, name: str, value: object, origin: str, anchor: dict | None = None):
    text = _clean(value)
    if not text:
        return
    evidence = {"value": text, "origin": origin, "status": "observed"}
    if anchor:
        evidence["anchor"] = anchor
    choices = metadata.setdefault(name, [])
    existing = next((c for c in choices if c["value"] == text and c["origin"] == origin), None)
    if existing is not None:
        return existing
    choices.append(evidence)
    return evidence


def pdf_metadata(path: Path, document: ParsedDocument | None = None) -> dict:
    """Read declarations only; a PDF producer is not its publisher."""
    from pypdf import PdfReader

    from gunther.asset_service import _limited_pdf_filters

    metadata: dict = {}
    with _limited_pdf_filters():
        reader = PdfReader(path, strict=False)
        info = reader.metadata or {}
        for key, name in {
            "/Title": "title",
            "/Author": "authors",
            "/Subject": "subject",
            "/Keywords": "keywords",
            "/Publisher": "publisher",
            "/CreationDate": "fileCreated",
            "/ModDate": "fileModified",
            "/Creator": "fileCreator",
            "/Producer": "fileProducer",
            "/doi": "doi",
        }.items():
            _field(metadata, name, info.get(key), "pdf-info:" + key)
        xmp = reader.xmp_metadata
        if xmp is not None and len(xmp.stream.get_data()) <= 2 * 1024 * 1024:
            tree = ET.fromstring(xmp.stream.get_data())
            # Standard namespaces prevent unrelated custom fields from overriding metadata.
            for item in tree.iter():
                namespace, _, local = item.tag.lstrip("{").partition("}")
                if namespace == "http://purl.org/dc/elements/1.1/":
                    name = {
                        "title": "title",
                        "creator": "authors",
                        "publisher": "publisher",
                        "source": "source",
                        "description": "subject",
                        "date": "issued",
                        "identifier": "identifier",
                        "rights": "rights",
                    }.get(local)
                elif namespace.startswith("http://prismstandard.org/namespaces/basic/"):
                    name = {
                        "doi": "doi",
                        "publicationName": "publication",
                        "publicationDate": "published",
                        "coverDate": "coverDate",
                        "url": "source",
                        "issn": "issn",
                        "isbn": "isbn",
                        "copyright": "rights",
                    }.get(local)
                else:
                    name = None
                if name:
                    values = [child.text for child in item.iter() if not len(child) and child.text]
                    _field(metadata, name, "; ".join(values), "xmp:" + local)
        if document is not None:
            _native_hierarchy(reader, document)
    return metadata


def _native_hierarchy(reader, document: ParsedDocument) -> None:
    """Partial bookmarks calibrate matching font styles on the remaining pages.

    This avoids ranking a later chapter below unrelated fonts in a supplementary
    document. No semantic section-name vocabulary participates in this step.
    """
    bookmarks: dict[tuple[int, str], int] = {}

    def visit_outline(nodes, level=1):
        for node in nodes:
            if isinstance(node, list):
                visit_outline(node, level + 1)
            else:
                page = reader.get_destination_page_number(node)
                if page is not None:
                    bookmarks[(page + 1, normalized(node.title))] = level

    with suppress(Exception):
        # Broken optional bookmarks cannot invalidate the document.
        visit_outline(reader.outline)
    page_headings = defaultdict(list)
    for element in document.elements:
        if element.kind == "heading" and isinstance(element.anchor.get("page"), int):
            page_headings[element.anchor["page"]].append(element)
    anchored_styles = defaultdict(Counter)
    styles = {}
    for number, headings in page_headings.items():
        runs = []
        length = 0

        def visit_text(text, cm, tm, font, size, runs=runs):
            nonlocal length
            key = normalized(text)
            if not key or not font or abs(tm[1]) > 0.01 or abs(tm[2]) > 0.01:
                return
            name = re.sub(r"^/?[A-Z]{6}\+", "", str(font.get("/BaseFont", "")))
            actual_size = round(abs(float(size) * tm[3] * cm[3]) * 2) / 2
            runs.append((length, length + len(key), key, (name, actual_size)))
            length += len(key)

        try:
            reader.pages[number - 1].extract_text(visitor_text=visit_text)
        except Exception:
            continue
        text = "".join(run[2] for run in runs)
        for element in headings:
            key = normalized(element.text)
            start = text.find(key)
            if start >= 0:
                stop = start + len(key)
                weights = Counter()
                for left, right, _, style in runs:
                    weights[style] += max(0, min(right, stop) - max(left, start))
                if weights and weights.most_common(1)[0][1] > 0:
                    styles[id(element)] = weights.most_common(1)[0][0]
            if (level := bookmarks.get((number, key))) is not None:
                element.payload.update(level=level, structureSource="pdf-bookmark")
                if id(element) in styles:
                    anchored_styles[styles[id(element)]][level] += 1
    for element in document.elements:
        levels = anchored_styles.get(styles.get(id(element)))
        if (
            element.kind == "heading"
            and element.payload.get("structureSource") != "pdf-bookmark"
            and levels
            and len(levels) == 1
        ):
            element.payload.update(
                level=next(iter(levels)), structureSource="bookmark-calibrated-typography"
            )
    # Changing a heading level must update parent links and evidence paths too.
    stack = []
    refs = {e.payload.get("documentRef"): i for i, e in enumerate(document.elements)}
    for index, element in enumerate(document.elements):
        if element.kind == "heading":
            level = int(element.payload.get("level", 2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            element.parent = stack[-1][1] if stack else None
            stack.append((level, index, element.text))
        elif element.kind in {"page_header", "page_footer", "metadata"}:
            element.parent = None
            element.headings = []
            continue
        else:
            parent = refs.get(element.payload.get("visualOwner"))
            element.parent = (
                parent
                if parent is not None and parent < index
                else (stack[-1][1] if stack else None)
            )
        element.headings = [h[2] for h in stack]


def organize_document(document: ParsedDocument, metadata: dict | None = None) -> None:
    """Retain observed roles and make section hierarchy available to every consumer."""
    metadata = metadata if metadata is not None else document.manifest.get("metadata", {})
    page_roles(document)
    title_keys = {normalized(c["value"]) for c in metadata.get("title", [])}
    author_keys = [
        normalized(name) for c in metadata.get("authors", []) for name in c["value"].split(";")
    ]
    outline = []
    for ordinal, element in enumerate(document.elements):
        notice = PUBLICATION_NOTICE.match(element.text.strip())
        notice_context = (
            element.kind == "reference"
            or element.text.lstrip().startswith("©")
            or (notice and element.text.strip()[notice.end() :].lstrip().startswith((":", "：")))
        )
        if (
            element.kind in {"paragraph", "reference", "metadata"}
            and not element.payload.get("marker")
            and notice
            and (notice_context or element.payload.get("role") == "rights")
        ):
            # A trailing licence or publisher notice does not become a reference
            # merely because it follows the bibliography without another heading.
            element.kind = "metadata"
            element.payload.update(role="rights", noEmbed=True)
            _field(metadata, "rights", element.text, "document", element.anchor)
        if element.kind in NON_BODY:
            element.payload["noEmbed"] = True
            element.payload.pop("reference", None)
        if element.kind == "heading":
            if normalized(element.text) in title_keys or element.payload.get("role") == "title":
                element.payload["role"] = "title"
                _field(metadata, "title", element.text, "document", element.anchor)
            else:
                outline.append(
                    {
                        "ordinal": ordinal,
                        "title": element.text,
                        "level": element.payload.get("level", 2),
                        "anchor": element.anchor,
                        "status": "observed"
                        if element.payload.get("structureSource") == "pdf-bookmark"
                        else "inferred",
                    }
                )
        if element.kind not in {"paragraph", "metadata", "page_header", "page_footer"}:
            continue
        if match := DATE_LABEL.fullmatch(element.text.strip()):
            if DATE_VALUE.search(match[2]):
                key = match[1].casefold().replace(" ", "")
                key = {
                    "publishedonline": "published",
                    "publicationdate": "published",
                    "出版日期": "published",
                    "发布日期": "published",
                    "接收日期": "accepted",
                    "修订日期": "updated",
                }.get(key, key)
                evidence = _field(
                    metadata,
                    key,
                    match[2],
                    str(element.payload.get("extraction", "document")),
                    element.anchor,
                )
                if evidence is not None and isinstance(
                    element.payload.get("confidence"), (int, float)
                ):
                    evidence["confidence"] = element.payload["confidence"]
                element.kind = "metadata"
                element.payload.update(role=key, noEmbed=True)
            else:
                # A placeholder stays visible in details, never becomes a claimed date.
                element.kind = "metadata"
                element.payload.update(role="date-notice", noEmbed=True, status="unresolved")
        elif element.kind == "paragraph" and element.anchor.get("page") == 1:
            text_key = re.sub(r"\d", "", normalized(element.text))
            remaining = text_key
            for key in sorted(set(author_keys), key=len, reverse=True):
                if key:
                    remaining = remaining.replace(key, "")
            # Mentioning a declared author in prose does not make it an author line.
            named_fraction = 1 - len(remaining) / max(1, len(text_key))
            if len(text_key) > 12 and named_fraction >= 0.7:
                element.kind = "metadata"
                element.payload.update(role="authors", noEmbed=True)
        if element.kind == "paragraph":
            for name in ("doi", "identifier"):
                if any(
                    c["value"] in element.text
                    and len(element.text.replace(c["value"], "").strip()) < 40
                    for c in metadata.get(name, [])
                ):
                    element.kind = "metadata"
                    element.payload.update(role="identifier", noEmbed=True)
    document.manifest.update(
        metadata=metadata,
        outline=outline,
        structure={
            "policy": "source-defined",
            "headings": len(outline),
            "unheadedContent": not bool(outline),
            "inferredSectionNames": False,
        },
    )
    # A sentence interrupted by a page or column break continues in the next
    # paragraph. Only page furniture and floats (visuals, tables) may sit between
    # the two parts; equations, lists and headings end the sentence's flow.
    notes = _page_notes(document)
    flow: dict[object, list[tuple[int, int, int, int]]] = defaultdict(list)
    for element in document.elements:
        skip = element.kind in CONTINUATION_SKIPPABLE or id(element) in notes
        if not skip and (box := _box(element)):
            flow[element.anchor.get("page")].append(box)
    previous = None
    for element in document.elements:
        element.payload.pop("continuesDocumentRef", None)
        if element.kind in CONTINUATION_SKIPPABLE or id(element) in notes:
            continue
        if element.kind != "paragraph":
            previous = None
            continue
        if previous is not None and _continues(previous, element, flow):
            element.payload["continuesDocumentRef"] = previous.payload["documentRef"]
        previous = element
    mark_abstract(document)
    document.validate()


ABSTRACT_HEADING = re.compile(
    r"^(?:abstract|summary|synopsis|résumé|resumen|zusammenfassung|摘要|概要|要旨)$", re.I
)
NOTE_MARKER = re.compile(r"^\s*(?:[*†‡§¶#]|\d{1,2}\s*[A-Z])")


def _demote(element: Any, kind: str, role: str) -> None:
    element.payload.setdefault("sourceKind", element.kind)
    element.kind = kind
    element.payload.pop("level", None)
    element.payload.update(role=role, noEmbed=True)


def page_roles(document: ParsedDocument) -> None:
    """Separate what the page prints around the text from the text itself.

    Evidence is repetition and geometry: running heads that recur at a page
    edge, uncaptioned images at the page edge or on several pages, small-type
    notes closing a page, and narrow small-type margin columns beside the body.
    Every element is kept with its original kind; only its role changes.
    """
    elements = document.elements
    body = body_font_size(document)

    def band(e):
        box = _box(e)
        if not box:
            return None
        return "top" if box[3] < 120_000 else "bottom" if box[1] > 900_000 else None

    # Running heads and feet: the same short text at a page edge on several
    # pages, or text the layout already recognised as a page header elsewhere.
    known = {
        normalized(e.text) for e in elements
        if e.kind in {"page_header", "page_footer"} and normalized(e.text)
    }
    pages_by_text: dict[tuple, set] = defaultdict(set)
    for e in elements:
        if (edge := band(e)) and len(e.text) <= 120 and normalized(e.text):
            pages_by_text[(normalized(e.text), edge)].add(e.anchor.get("page"))
    largest = max(
        (float(e.payload.get("fontSize") or 0) for e in elements if e.kind == "heading"),
        default=0,
    )
    for e in elements:
        edge, box, key = band(e), _box(e), normalized(e.text)
        # A masthead can print a running label lower on the opening page.
        known_head = key in known and len(e.text) <= 40 and box and box[3] < 250_000
        if (
            e.kind in {"heading", "paragraph"} and key and (edge or known_head)
            and not (largest and float(e.payload.get("fontSize") or 0) >= largest)
            and (known_head or key in known or len(pages_by_text[(key, edge)]) >= 2)
        ):
            _demote(e, "page_header" if edge != "bottom" else "page_footer", "running-text")

    # Images without a caption that sit at a page edge or recur on several
    # pages (logos, journal mastheads, licence badges) are not the document's
    # figures; they stay available with the document details.
    def captioned(e):
        ref = e.payload.get("documentRef")
        return bool(
            e.payload.get("caption") or e.payload.get("captionRefs") or e.payload.get("captionRef")
            or any(c.kind == "caption" and c.payload.get("visualOwner") == ref for c in elements)
        )

    figures = [e for e in elements if e.kind == "figure" and _box(e)]
    numbered = any(CAPTION_LABEL.match(str(e.payload.get("caption", ""))) for e in figures)
    for e in figures:
        if captioned(e):
            continue
        box = _box(e)
        area = (box[2] - box[0]) * (box[3] - box[1])
        recurring = any(
            other is not e and other.anchor.get("page") != e.anchor.get("page")
            and all(abs(a - b) < 20_000 for a, b in zip(_box(other), box, strict=True))
            for other in figures
        )
        if band(e) or box[3] < 150_000 or recurring or (numbered and area < 20_000_000_000):
            _demote(e, "embedded_image", "decoration")
            ref = e.payload.get("documentRef")
            for other in elements:
                if other.payload.get("visualOwner") == ref and other.kind == "figure_text":
                    other.payload["role"] = "decoration-text"

    # A lone glyph or a string without letters is not a paragraph: brackets of
    # a formula, panel letters, invisible font-check strings.
    for e in elements:
        stripped = re.sub(r"\s", "", e.text)
        if e.kind == "paragraph" and not e.payload.get("marker") and (
            len(stripped) <= 2 or (len(stripped) <= 24 and not re.search(r"[^\W\d_]", stripped))
        ):
            _demote(e, "figure_text", "stray-glyphs")

    # A bare link printed under a caption (a figure's own DOI) belongs to it,
    # as does a bare link set in small type away from it.
    previous = None
    for e in elements:
        size = e.payload.get("fontSize")
        if (
            e.kind == "paragraph"
            and re.fullmatch(r"(?:https?://|doi:)\S+", e.text.strip(), re.I)
            and (
                (previous is not None and previous.kind in {"caption", "figure", "table"})
                or (body and size and size < body * 0.95)
            )
        ):
            _demote(e, "figure_text", "caption-link")
            if previous is not None and previous.kind in {"caption", "figure", "table"} and (
                owner := previous.payload.get("visualOwner", previous.payload.get("documentRef"))
            ):
                e.payload["visualOwner"] = owner
        if e.kind not in {"figure_text", "figure_panel", "page_header", "page_footer"}:
            previous = e

    # Small-type notes closing a page: affiliations and correspondence on the
    # opening page, and marked footnotes on any page.
    first_page = min(
        (e.anchor.get("page") for e in elements
         if isinstance(e.anchor.get("page"), int) and e.kind not in NON_BODY),
        default=1,
    )
    notes = [e for e in elements if id(e) in _page_notes(document)]
    note_pages = {
        e.anchor.get("page") for e in notes
        if e.anchor.get("page") == first_page or NOTE_MARKER.match(e.text)
        or any(s.get("start") == 0 and s.get("script") == "super"
               for s in e.payload.get("inlineSpans", []))
    }
    for e in notes:
        if e.anchor.get("page") in note_pages:
            _demote(e, "footnote", "page-note")
    # Marked notes at the foot of the opening page can share the body's size.
    for e in elements:
        box = _box(e)
        if (
            e.kind == "paragraph" and box and e.anchor.get("page") == first_page
            and box[1] > 850_000 and NOTE_MARKER.match(e.text)
            and not any(
                _box(o) and _box(o)[1] > box[3] and o.kind == "paragraph"
                and o.anchor.get("page") == first_page for o in elements
            )
        ):
            _demote(e, "footnote", "page-note")

    # Margin columns: small type in a strip much narrower than the body column
    # and beside it, never overlapping it horizontally.
    if not body:
        return
    prose: dict[object, list[tuple]] = defaultdict(list)
    for e in elements:
        size = e.payload.get("fontSize")
        if e.kind == "paragraph" and len(e.text) > 200 and size and size >= body * 0.95 and _box(e):
            prose[e.anchor.get("page")].append(_box(e))
    def first_box(e):
        # The element's own region; a layout merge may have appended text
        # from elsewhere on the page.
        b = e.anchor.get("bboxPpm")
        if not isinstance(b, list) or len(b) != 4:
            return None
        return (b[0], b[1], b[0] + b[2], b[1] + b[3])

    for e in elements:
        columns = prose.get(e.anchor.get("page"))
        size, box = e.payload.get("fontSize"), first_box(e)
        if not (columns and box and size and size < body * 0.95):
            continue
        if e.kind not in {"paragraph", "heading", "list_item"} or e.payload.get("visualOwner"):
            continue
        width = sorted(b[2] - b[0] for b in columns)[len(columns) // 2]
        beside = all(box[2] <= b[0] or box[0] >= b[2] for b in columns)
        if beside and box[2] - box[0] < 0.6 * width:
            _demote(e, "sidebar", "margin-column")
    for e in figures:
        columns = prose.get(e.anchor.get("page"))
        box = _box(e)
        if e.kind == "figure" and columns and not captioned(e):
            width = sorted(b[2] - b[0] for b in columns)[len(columns) // 2]
            beside = all(box[2] <= b[0] or box[0] >= b[2] for b in columns)
            if box[2] - box[0] < 0.6 * width and beside:
                _demote(e, "embedded_image", "decoration")


def mark_abstract(document: ParsedDocument) -> None:
    """The opening summary of a paper, so readers can start with it.

    A heading naming the abstract is used when present. Otherwise the first
    long paragraph on the title page, before any section heading, is the
    abstract only in a document that also has a title and a bibliography.
    The role is recorded as inferred; the text is unchanged.
    """
    elements = document.elements
    positions = {id(e): i for i, e in enumerate(elements)}

    def index_of(e):
        return positions[id(e)]

    for e in elements:
        if e.payload.get("role") == "abstract":
            e.payload.pop("role")
            e.payload.pop("abstractSource", None)
    title = next((e for e in elements if e.payload.get("role") == "title"), None)
    body = body_font_size(document)
    start = None
    for index, e in enumerate(elements):
        if e.kind == "heading" and ABSTRACT_HEADING.match(e.text.strip().rstrip(".:：")):
            start = index
            for follower in elements[index + 1:]:
                if follower.kind == "heading":
                    break
                if follower.kind == "paragraph":
                    follower.payload.update(role="abstract", abstractSource="heading")
            break
    if start is None:
        if title is None or not any(e.kind == "reference" for e in elements):
            return
        for index, e in enumerate(elements[elements.index(title) + 1:], elements.index(title) + 1):
            if e.anchor.get("page") != title.anchor.get("page") or e.kind == "heading":
                return
            size = e.payload.get("fontSize")
            if (
                e.kind == "paragraph" and len(e.text) >= 300 and not e.payload.get("visualOwner")
                and not (size and body and size < body * 0.95)
            ):
                e.payload.update(role="abstract", abstractSource="first-page-position")
                start = index
                break
        else:
            return
    # Short labels printed before the title on its page form the masthead.
    if title is not None:
        for e in elements[:elements.index(title)]:
            if (
                e.kind in {"paragraph", "heading"} and len(e.text) < 80
                and e.anchor.get("page") == title.anchor.get("page")
            ):
                _demote(e, "page_header", "masthead")
    # Small type on the title page before the first section is front matter
    # (affiliations, correspondence, keywords), shown with the document details.
    if title is None or not body or elements.index(title) > start:
        return
    for e in elements[elements.index(title) + 1:]:
        if e.kind == "heading" and e.payload.get("role") != "title" and index_of(e) > start:
            break
        size = e.payload.get("fontSize")
        if (
            e.kind in {"paragraph", "list_item"} and size and size < body * 0.95
            and e.anchor.get("page") == title.anchor.get("page")
        ):
            e.payload.setdefault("sourceKind", e.kind)
            e.kind = "metadata"
            e.payload.update(role="front-matter", noEmbed=True)


CONTINUATION_SKIPPABLE = {
    "page_header", "page_footer", "page_number", "footnote", "metadata",
    "figure", "figure_text", "figure_panel", "embedded_image", "caption", "table",
}


def _page_notes(document: ParsedDocument) -> set[int]:
    """Small-type blocks closing a page, such as affiliations or footnotes.

    Typography and position together are the evidence: text set clearly smaller
    than the running prose, in the lower part of a page, with no prose below it.
    """
    body = body_font_size(document)
    if not body:
        return set()
    prose: dict[object, list[int]] = defaultdict(list)
    for e in document.elements:
        size, box = e.payload.get("fontSize"), _box(e)
        if box and e.kind in {"paragraph", "list_item"} and size and size >= body * 0.95:
            prose[e.anchor.get("page")].append(box[1])
    notes = set()
    for e in document.elements:
        size, box = e.payload.get("fontSize"), _box(e)
        if (
            e.kind == "paragraph" and box and size and size < body * 0.95
            and box[1] > 600_000
            and not any(top > box[3] for top in prose[e.anchor.get("page")])
        ):
            notes.add(id(e))
    return notes


def _continues(previous: Any, element: Any, flow: dict) -> bool:
    """The first part ends its column and the second opens a later column."""
    pb, cb = _box(previous), _box(element)
    page, next_page = previous.anchor.get("page"), element.anchor.get("page")
    if not (pb and cb and isinstance(page, int) and isinstance(next_page, int)):
        return False

    def overlaps(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 0.5 * min(a[2] - a[0], b[2] - b[0])

    ends_column = not any(
        box != pb and overlaps(box, pb) and box[1] >= pb[3] - 2_000 for box in flow[page]
    )
    opens_column = not any(
        box != cb and overlaps(box, cb) and box[3] <= cb[1] + 2_000 for box in flow[next_page]
    )
    later_column = next_page == page + 1 or (next_page == page and cb[0] >= pb[2] - 2_000)
    sizes = previous.payload.get("fontSize"), element.payload.get("fontSize")
    same_type = None in sizes or abs(float(sizes[0]) - float(sizes[1])) < 0.5
    return bool(
        ends_column and opens_column and later_column and same_type
        and previous.headings == element.headings
        and len(previous.text) > 100 and re.match(r"^[(\[]?[a-z0-9]", element.text)
        and not re.search(r"[.!?。！？:：;；][\"'”’)]*$", previous.text.rstrip())
        and previous.payload.get("documentRef")
    )

def in_reference_section(headings: list[str]) -> bool:
    """A bibliography ends at the next heading, regardless of inferred level.

    An ancestor can remain on the layout stack when a later appendix has a
    deeper inferred level. Only the current heading determines this span.
    """
    from gunther.pdf_text import REFERENCE_SECTIONS, section_name

    return bool(headings and section_name(headings[-1]) in REFERENCE_SECTIONS)


def _box(element) -> tuple[int, int, int, int] | None:
    box = element.anchor.get("bboxPpm")
    if not isinstance(box, list) or len(box) != 4:
        return None
    boxes = [
        box,
        *[
            span["bboxPpm"]
            for span in element.anchor.get("spans", [])
            if span.get("page") == element.anchor.get("page") and "bboxPpm" in span
        ],
    ]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[0] + b[2] for b in boxes),
        max(b[1] + b[3] for b in boxes),
    )


def _contains(outer: tuple, inner: tuple) -> bool:
    overlap = max(0, min(outer[2], inner[2]) - max(outer[0], inner[0])) * max(
        0, min(outer[3], inner[3]) - max(outer[1], inner[1])
    )
    area = max(1, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return overlap / area >= 0.85


def body_font_size(document: ParsedDocument) -> float | None:
    """The size carrying most running prose, from native glyph evidence only."""
    fonts = Counter()
    for e in document.elements:
        size = e.payload.get("fontSize")
        prose = e.kind == "paragraph" and len(e.text) > 200
        if prose and size and not e.payload.get("visualOwner"):
            fonts[size] += len(e.text)
    return fonts.most_common(1)[0][0] if fonts else None


def recover_figure_regions(document: ParsedDocument) -> bool:
    """Complete caption-led regions when layout detection found only one panel.

    Caption typography and the absence of body prose bound expansion. A short
    string alone is never evidence that text belongs to a diagram. Returns
    whether a heading changed role, so the caller can rebuild heading paths.
    """
    elements = document.elements
    body_size = body_font_size(document)
    captions = []
    original_kinds = {id(e): e.kind for e in elements}
    for e in elements:
        match = CAPTION_LABEL.match(e.text.strip())
        if (
            e.kind in {"paragraph", "heading", "caption"} and _box(e) and match
            and re.match(r"\s*[|:：.‒–—]", e.text.strip()[match.end():])
            and not re.search(r"(?:^|\s)(?:table|tabelle|表)\s*", match[0], re.I)
        ):
            e.payload.setdefault("sourceKind", e.kind)
            e.kind = "caption"
            e.payload.pop("level", None)
            captions.append(e)
    caption_ids = {id(e) for e in captions}
    visuals = [e for e in elements if e.kind == "figure" and _box(e)]
    assigned: dict[str, list] = defaultdict(list)
    for caption in captions:
        cb, page = _box(caption), caption.anchor.get("page")
        declared = [
            e for e in visuals if caption.payload["documentRef"] in {
                *e.payload.get("captionRefs", []), e.payload.get("captionRef")
            }
        ]
        candidates = [
            e for e in visuals if e.anchor.get("page") == page
            and _box(e)[1] < cb[1]
            and _box(e)[3] <= cb[1] + 25_000
            and min(_box(e)[2], cb[2]) > max(_box(e)[0], cb[0])
        ]
        if len(declared) == 1:
            owner = declared[0]
        elif declared:
            continue  # Shared explicit ownership remains with the panel grouping pass.
        elif candidates:
            owner = min(candidates, key=lambda e: abs(_box(e)[3] - cb[1]))
        else:
            # A full-page plate can have its caption on the adjoining page.
            adjacent = None
            if isinstance(page, int):
                adjacent = page + 1 if cb[1] > 600_000 else page - 1 if cb[1] < 200_000 else None
            label = CAPTION_LABEL.match(caption.text.strip())
            prefix = caption.text.strip()[:label.end()] if label else caption.text.strip()
            candidates = [
                e for e in visuals if e.anchor.get("page") == adjacent
                and (_box(e)[2] - _box(e)[0]) * (_box(e)[3] - _box(e)[1]) > 150_000_000_000
                # A plate that already declares a different caption is not this one's.
                and not (
                    CAPTION_LABEL.match(str(e.payload.get("caption", "")))
                    and not str(e.payload.get("caption", "")).startswith(prefix)
                )
            ]
            owner = candidates[0] if len(candidates) == 1 else None
        if owner is None:
            continue
        ref = owner.payload["documentRef"]
        assigned[ref].append(caption)
        size = caption.payload.get("fontSize")
        for other in elements:
            ob = _box(other)
            first = other.anchor.get("bboxPpm")
            first_box = first and (first[0], first[1], first[0] + first[2], first[1] + first[3])
            if (
                id(other) in caption_ids or not ob
                or other.anchor.get("page") != page
                or other.kind not in {"paragraph", "caption"}
                or other.payload.get("visualOwner")
                or len(other.text) < 60
            ):
                continue
            same_font = size and abs(float(other.payload.get("fontSize", 0)) - size) < 0.4
            aligned = abs(ob[1] - cb[1]) < 8_000 and ob[0] >= cb[2] - 10_000
            below = (
                original_kinds[id(caption)] == "heading"
                and first_box and 0 <= first_box[1] - cb[3] < 15_000
                and abs(first_box[0] - cb[0]) < 5_000
            )
            smaller = same_font and body_size and size < body_size * 0.95
            if (aligned or below) and (other.kind == "caption" or smaller):
                other.payload.setdefault("sourceKind", other.kind)
                other.kind = "caption"
                assigned[ref].append(other)
        # A caption can span multiple columns in a single layout object, or several
        # objects. Reading order comes from its existing provenance, then column x.
        assigned[ref] = list({id(e): e for e in assigned[ref]}.values())

    for visual in visuals:
        owner = visual.payload["documentRef"]
        linked = assigned.get(owner, [])
        if not linked:
            continue
        linked_ids = {id(e) for e in linked}
        linked.sort(key=lambda e: (e.anchor.get("page", 0), _box(e)[0], _box(e)[1]))
        caption = ""
        for part in (e.text.strip() for e in linked):
            # Column-split parts of one sentence rejoin; complete ones keep a break.
            ends = re.search(r"[.!?。！？:：][\"'”’)]*$", caption)
            caption += ("\n" if ends else " ") + part if caption else part
        visual.payload.update(
            caption=caption,
            captionRefs=[e.payload["documentRef"] for e in linked],
            captionRef=linked[0].payload["documentRef"],
        )
        for e in linked:
            e.payload["visualOwner"] = owner
        same_page = [e for e in linked if e.anchor.get("page") == visual.anchor.get("page")]
        vb = _box(visual)
        region = vb
        if same_page and body_size:
            top = min(_box(e)[1] for e in same_page)
            left = min(vb[0], *(_box(e)[0] for e in same_page))
            right = max(vb[2], *(_box(e)[2] for e in same_page))
            page_items = [
                e for e in elements if e.anchor.get("page") == visual.anchor.get("page") and _box(e)
            ]

            def body(e):
                return (
                    e.kind in {"paragraph", "list_item"} and len(e.text) > 100
                    and not e.payload.get("visualOwner")
                    and e.payload.get("fontSize", body_size) >= body_size * 0.95
                )

            def boundary(e):
                # Prose, tables and other visuals' captions bound the region.
                text = e.text.strip()
                labelled = e.kind in {"caption", "paragraph"} and CAPTION_LABEL.match(text)
                return body(e) or e.kind == "table" or bool(labelled)

            above = [
                _box(e)[3] for e in page_items
                if boundary(e) and id(e) not in linked_ids and _box(e)[3] <= vb[1]
                and min(_box(e)[2], right) > max(_box(e)[0], left)
            ]
            start = max(above, default=0)
            labels = [
                e for e in page_items
                if e.kind in {"paragraph", "heading", "figure_text", "caption", "equation"}
                and id(e) not in linked_ids and id(e) not in caption_ids and not boundary(e)
                and start < _box(e)[1] and _box(e)[3] < top
                and left - 10_000 <= _box(e)[0] and _box(e)[2] <= right + 10_000
            ]
            proposed = (
                left, min([vb[1], *(_box(e)[1] for e in labels)]), right,
                max(vb[3], top - 4_000),
            )
            eats_body = any(
                boundary(e) and id(e) not in linked_ids and _contains(proposed, _box(e))
                for e in page_items
            )
            if proposed != vb and not eats_body:
                region = proposed
                visual.anchor.setdefault("sourceBboxPpm", visual.anchor["bboxPpm"])
                visual.anchor.update(
                    bboxPpm=[region[0], region[1], region[2] - region[0], region[3] - region[1]],
                    cropSource="caption-and-typography",
                )
        for other in elements:
            if (
                other.kind in {"paragraph", "heading", "caption", "equation"}
                and id(other) not in linked_ids and id(other) not in caption_ids
                and other.anchor.get("page") == visual.anchor.get("page")
                and _box(other) and _edge_label(other, region, body_size)
                and other.payload.get("visualOwner") in {None, owner}
            ):
                other.payload.setdefault("sourceKind", other.kind)
                other.kind = "figure_text"
                other.payload.update(visualOwner=owner, noEmbed=True)
    return any(
        e.kind != "heading" and e.payload.get("sourceKind") == "heading" for e in elements
    )


def _edge_label(element: Any, region: tuple, body_size: float | None) -> bool:
    """Text inside a visual's region, or a label touching its edge.

    Panel letters and axis titles often sit just outside the detected box. Only
    text set smaller than body prose, or a lone panel letter, is taken from the
    margin; the geometry is always required.
    """
    box = _box(element)
    if _contains(region, box):
        return True
    pad = 12_000
    padded = (region[0] - pad, region[1] - pad, region[2] + pad, region[3] + pad)
    size = element.payload.get("fontSize")
    small = bool(body_size and size and size < body_size * 0.95)
    letter = bool(re.fullmatch(r"\(?[A-Za-z]\)?", element.text.strip()))
    return _contains(padded, box) and (small or letter)


def group_visuals(document: ParsedDocument) -> None:
    """Join panels only when an external caption and geometry support ownership.

    Keep panel text and structured tables as children. A chart's numeric labels
    never become reconstructed table values or body paragraphs.
    """

    def caption_kind(text):
        if not CAPTION_LABEL.match(text.strip()):
            return None
        return "table" if re.match(r"^(?:table|表|tabelle)", text.strip(), re.I) else "figure"

    def caption_refs(element):
        return list(
            dict.fromkeys(
                filter(
                    None,
                    [
                        *element.payload.get("captionRefs", []),
                        element.payload.get("captionRef"),
                    ],
                )
            )
        )

    objects = {
        e.payload["documentRef"]: e for e in document.elements if e.payload.get("documentRef")
    }
    linked_captions = defaultdict(set)
    for element in document.elements:
        if element.kind not in {"figure", "table"}:
            continue
        refs = caption_refs(element)
        rejected = [
            ref
            for ref in refs
            if ref in objects and caption_kind(objects[ref].text) not in {None, element.kind}
        ]
        if rejected:
            # Preserve the parser's claim, but never use a contradictory link
            # as active ownership. The caption itself remains unchanged.
            element.payload.setdefault("sourceCaptionRefs", refs)
            element.payload.setdefault("sourceCaption", element.payload.get("caption", ""))
            element.payload["captionLinkIssues"] = [
                {"captionRef": ref, "reason": "caption-type-mismatch"} for ref in rejected
            ]
            refs = [ref for ref in refs if ref not in rejected]
            element.payload["captionRefs"] = refs
            element.payload.pop("captionRef", None)
            if len(refs) == 1:
                element.payload["captionRef"] = refs[0]
            element.payload["caption"] = "\n".join(
                objects[ref].text for ref in refs if ref in objects
            )
        for ref in refs:
            linked_captions[ref].add(element.payload.get("documentRef"))
    for element in document.elements:
        if element.kind != "caption":
            continue
        owner = objects.get(element.payload.get("visualOwner"))
        if owner is not None and caption_kind(element.text) not in {None, owner.kind}:
            element.payload.setdefault("sourceVisualOwner", element.payload.pop("visualOwner"))
        owners = linked_captions.get(element.payload.get("documentRef"), set())
        if len(owners) == 1 and None not in owners:
            element.payload.setdefault("visualOwner", next(iter(owners)))
    by_page = defaultdict(list)
    for index, element in enumerate(document.elements):
        box = _box(element)
        if (
            element.kind == "figure"
            and box
            and (box[2] - box[0]) * (box[3] - box[1]) < 2_000_000_000
            and not element.payload.get("caption")
            and not element.payload.get("captionRefs")
            and not element.payload.get("captionRef")
            and not any(
                c.payload.get("visualOwner") == element.payload.get("documentRef")
                for c in document.elements
            )
        ):
            element.kind = "embedded_image"
            element.payload.update(role="unclassified-image", noEmbed=True)
        if element.anchor.get("page") and _box(element):
            by_page[element.anchor["page"]].append(index)
    for indices in by_page.values():
        visuals = [
            i
            for i in indices
            if document.elements[i].kind in {"figure", "table"}
            and not document.elements[i].payload.get("visualOwner")
        ]
        if not visuals:
            continue
        free_visuals = [
            i
            for i in visuals
            if not document.elements[i].payload.get("caption")
            and not document.elements[i].payload.get("captionRefs")
            and not document.elements[i].payload.get("captionRef")
        ]
        captions = []
        seeds = {}
        for index in indices:
            element = document.elements[index]
            if element.kind != "caption":
                continue
            owners = linked_captions.get(element.payload.get("documentRef"), set())
            if owners:
                members = [
                    i for i in visuals if document.elements[i].payload.get("documentRef") in owners
                ]
                # Only a single shared, external figure caption can grow a panel
                # group. Multi-caption and cross-page links remain as supplied.
                if len(members) != len(owners) or any(
                    document.elements[i].kind != "figure"
                    or len(caption_refs(document.elements[i])) != 1
                    or _box(element)[1] < _box(document.elements[i])[3] - 25_000
                    for i in members
                ):
                    continue
                seeds[index] = members
                captions.append(index)
            elif element.payload.get("visualOwner"):
                continue
            elif any(_contains(_box(document.elements[v]), _box(element)) for v in visuals):
                element.kind = "figure_text"
                element.payload["noEmbed"] = True
            elif len(element.text) >= 40 or CAPTION_LABEL.match(element.text):
                captions.append(index)
        numbered = [c for c in captions if CAPTION_LABEL.match(document.elements[c].text)]
        if numbered:
            captions = numbered
        groups = defaultdict(list)
        for caption_index, members in seeds.items():
            groups[caption_index].extend(members)
        for index in free_visuals:
            visual = document.elements[index]
            box = _box(visual)
            candidates = []
            for c in captions:
                caption = document.elements[c]
                cb = _box(caption)
                if not (
                    cb[1] >= box[3] - 25_000
                    and cb[1] - box[3] < 550_000
                    and min(cb[2], box[2]) > max(cb[0], box[0])
                    and caption_kind(caption.text) in {None, visual.kind}
                ):
                    continue
                if c in seeds:
                    # A linked caption may cover an adjacent unlinked panel,
                    # but must span both columns and the panels' vertical bands
                    # must overlap. A nearby legend or another row is not a panel.
                    seed_boxes = [_box(document.elements[i]) for i in seeds[c]]
                    if (
                        visual.kind != "figure"
                        or caption_kind(caption.text) != "figure"
                        or any(
                            max(0, min(cb[2], b[2]) - max(cb[0], b[0])) < 0.85 * (b[2] - b[0])
                            for b in [box, *seed_boxes]
                        )
                        or not any(
                            min(box[3], b[3]) - max(box[1], b[1])
                            >= 0.5 * min(box[3] - box[1], b[3] - b[1])
                            and (box[2] <= b[0] or b[2] <= box[0])
                            for b in seed_boxes
                        )
                    ):
                        continue
                candidates.append(c)
            if candidates:
                caption = min(candidates, key=lambda c: _box(document.elements[c])[1] - box[3])
                groups[caption].append(index)
        for caption_index, members in groups.items():
            if caption_index in seeds and len(members) == 1:
                continue  # Preserve the original crop of a linked single visual.
            pictures = [i for i in members if document.elements[i].kind == "figure"]
            if not pictures:
                # A table caption identifies one table, never a combined crop.
                if len(members) == 1:
                    table = document.elements[members[0]]
                    caption = document.elements[caption_index]
                    table.payload.update(
                        caption=caption.text,
                        captionRef=caption.payload["documentRef"],
                    )
                    caption.payload["visualOwner"] = table.payload["documentRef"]
                continue
            if len(pictures) != len(members):
                continue
            boxes = [_box(document.elements[i]) for i in members]
            member_refs = {document.elements[i].payload["documentRef"] for i in members}
            caption_top = _box(document.elements[caption_index])[1]
            # Detection boxes often cover the plot but omit rotated axis labels
            # or panel letters. Include owned annotations above the real caption.
            boxes.extend(
                _box(document.elements[i])
                for i in indices
                if document.elements[i].payload.get("visualOwner") in member_refs
                and _box(document.elements[i])[3] <= caption_top
                and not CAPTION_LABEL.match(document.elements[i].text)
            )
            union = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            # Prose between panels invalidates a combined crop. Retain individual regions.
            if any(
                document.elements[i].kind == "paragraph"
                and len(document.elements[i].text) > 100
                and _contains(union, _box(document.elements[i]))
                for i in indices
            ):
                continue
            primary = document.elements[min(pictures)]
            owner = primary.payload["documentRef"]
            caption = document.elements[caption_index]
            primary.payload.update(
                caption=caption.text,
                captionRef=caption.payload["documentRef"],
                captionRefs=[caption.payload["documentRef"]],
                panelRefs=[document.elements[i].payload["documentRef"] for i in members],
            )
            left, top = max(0, union[0] - 2_000), max(0, union[1] - 2_000)
            right, bottom = min(1_000_000, union[2] + 2_000), min(caption_top, union[3] + 2_000)
            primary.anchor["bboxPpm"] = [left, top, right - left, bottom - top]
            primary.anchor["cropSource"] = "caption-associated-regions"
            for i in members:
                other = document.elements[i]
                previous_owner = other.payload["documentRef"]
                for child in document.elements:
                    if child.payload.get("visualOwner") == previous_owner:
                        child.payload["visualOwner"] = owner
                if other is not primary:
                    if other.payload.get("captionRefs") or other.payload.get("captionRef"):
                        other.payload.setdefault("sourceCaptionRefs", caption_refs(other))
                        other.payload.setdefault("sourceCaption", other.payload.get("caption", ""))
                        for key in ("caption", "captionRef", "captionRefs"):
                            other.payload.pop(key, None)
                    other.payload.update(visualOwner=owner, noEmbed=True)
                    if other.kind == "figure":
                        other.kind = "figure_panel"
            caption.payload["visualOwner"] = owner
            # Unassigned short labels inside the combined region belong to the visual too.
            for i in indices:
                other = document.elements[i]
                if (
                    other.kind in {"paragraph", "caption"}
                    and i != caption_index
                    and other.payload.get("documentRef") not in linked_captions
                    and not other.payload.get("visualOwner")
                    and _contains(union, _box(other))
                ):
                    other.kind = "figure_text"
                    other.payload.update(visualOwner=owner, noEmbed=True)
